from datetime import datetime

import deriv_connector as dc
import smc_analysis as smc
import zone_analysis as zones
import wave_analysis as wave
from config import Config

SWEEP_LOOKBACK = 40
CHOCH_MAX_WAIT = 6
MIN_RR = 1.5


def _get_bias(df) -> str:
    return smc.detect_structure(df)["trend"]


def _find_liquidity_target(df, direction):
    equal_level = smc.find_equal_levels(df, direction)
    if equal_level is not None:
        return equal_level, "Equal Highs/Lows (liquidity pool)"

    swings = smc.find_swing_points(df)
    current_price = float(df["close"].iloc[-1])

    if direction == "bullish":
        candidates = swings[swings["is_swing_high"]]["high"]
        candidates = candidates[candidates > current_price]
        if candidates.empty:
            return None, None
        return float(candidates.min()), "Nearest swing high"

    candidates = swings[swings["is_swing_low"]]["low"]
    candidates = candidates[candidates < current_price]
    if candidates.empty:
        return None, None
    return float(candidates.max()), "Nearest swing low"


def _find_sweep_and_choch(df, direction, lookback, max_wait):
    n = len(df)
    start = max(1, n - lookback)

    for i in range(n - 1, start - 1, -1):
        sub_df = df.iloc[: i + 1]
        sweep = smc.detect_liquidity_sweep(sub_df, check_last=1)
        sweep_ok = sweep["swept"] and (
            (direction == "bullish" and sweep["direction"] == "buy_side_taken")
            or (direction == "bearish" and sweep["direction"] == "sell_side_taken")
        )
        if not sweep_ok:
            continue

        confirm_end = min(n, i + max_wait + 1)
        for j in range(i, confirm_end):
            choch_df = df.iloc[: j + 1]
            structure = smc.detect_structure(choch_df)
            if structure["last_event"] == "CHoCH" and structure["trend"] == direction:
                return i, j

    return None


def _find_impulse_leg(df, sweep_index, choch_index, direction):
    segment = df.iloc[sweep_index: choch_index + 1]
    if direction == "bullish":
        start_price = float(df.iloc[sweep_index]["low"])
        end_price = float(segment["high"].max())
        wave_dir = "up"
    else:
        start_price = float(df.iloc[sweep_index]["high"])
        end_price = float(segment["low"].min())
        wave_dir = "down"

    return {"start_price": start_price, "end_price": end_price, "direction": wave_dir}


def _find_pd_array(df, direction):
    checks = [
        ("Order Block", smc.find_last_order_block(df, direction=direction)),
        ("Breaker Block", smc.find_breaker_block(df, direction=direction)),
        ("Mitigation Block", smc.find_mitigation_block(df, direction=direction)),
        ("Inversion FVG", smc.find_inversion_fvg(df, direction=direction)),
    ]
    return [(name, z) for name, z in checks if z is not None]


def _build_reason(path_label, direction_word, liquidity_source, sweep_choch_label,
                   pd_array_name, entry_zone, stop_source, target_price):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word}\n"
        f"Liquidity target: {liquidity_source} @ {round(target_price, 2)}\n"
        f"Manipulation: {sweep_choch_label}\n"
        f"Entry array: OTE (61.8%-79%) x {pd_array_name}\n"
        f"Entry range: {round(entry_zone['bottom'], 2)} - {round(entry_zone['top'], 2)}\n"
        f"Stop basis: {stop_source}"
    )


def _process_path(label, bias_direction_df, sweep_choch_df, entry_df, path_label,
                   entry_price_ref):
    direction_word = _get_bias(bias_direction_df)
    if direction_word not in ("bullish", "bearish"):
        return None, f"{path_label}: skipped - bias unclear on source timeframe"

    liquidity_price, liquidity_source = _find_liquidity_target(bias_direction_df, direction_word)
    if liquidity_price is None:
        return None, f"{path_label}: skipped - no liquidity target found"

    found = _find_sweep_and_choch(sweep_choch_df, direction_word, SWEEP_LOOKBACK, CHOCH_MAX_WAIT)
    if found is None:
        return None, f"{path_label}: skipped - no sweep+CHoCH sequence found"
    sweep_index, choch_index = found

    impulse = _find_impulse_leg(sweep_choch_df, sweep_index, choch_index, direction_word)
    ote_zone = wave.fibonacci_zone(impulse)

    pd_candidates = _find_pd_array(sweep_choch_df.iloc[: choch_index + 1], direction_word)
    if not pd_candidates:
        return None, f"{path_label}: skipped - no PD array (OB/Breaker/Mitigation/InvFVG) found"

    matched_pd = None
    entry_zone = None
    for name, pd_zone in pd_candidates:
        if zones.zones_overlap(ote_zone, pd_zone):
            top = min(ote_zone["top"], pd_zone["top"])
            bottom = max(ote_zone["bottom"], pd_zone["bottom"])
            if top >= bottom:
                matched_pd = name
                entry_zone = {"top": top, "bottom": bottom}
                break

    if entry_zone is None:
        return None, f"{path_label}: skipped - OTE does not overlap any PD array"

    current_price = float(entry_price_ref["close"].iloc[-1])
    if not zones.price_in_any_zone(current_price, [entry_zone]):
        return None, f"{path_label}: skipped - price not yet in OTE x {matched_pd} entry range"

    direction = "BUY" if direction_word == "bullish" else "SELL"
    entry_price = current_price

    sweep_candle = sweep_choch_df.iloc[sweep_index]
    if direction == "BUY":
        stop_loss = float(sweep_candle["low"]) * 0.9993
    else:
        stop_loss = float(sweep_candle["high"]) * 1.0007

    if direction == "BUY" and entry_price <= stop_loss:
        return None, f"{path_label}: skipped - stop already invalidated"
    if direction == "SELL" and entry_price >= stop_loss:
        return None, f"{path_label}: skipped - stop already invalidated"

    risk = abs(entry_price - stop_loss)
    reward = abs(liquidity_price - entry_price)
    rr = reward / risk if risk > 0 else 0
    if rr < MIN_RR:
        return None, f"{path_label}: skipped - R:R {round(rr, 2)} below minimum {MIN_RR}"

    take_profit = liquidity_price
    sweep_choch_label = f"Sweep then CHoCH confirmed"
    stop_source = "Behind sweep extreme"

    reason = _build_reason(
        path_label, direction_word, liquidity_source, sweep_choch_label,
        matched_pd, entry_zone, stop_source, take_profit,
    )

    signal = {
        "symbol": Config.SYMBOL,
        "direction": direction,
        "entry": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "reason": reason,
        "trade_label": path_label,
        "confidence": "high",
        "confidence_label": "منهج SMC كامل: تحيّز + سيولة + تلاعب + CHoCH + OTE x PD Array",
        "timestamp": datetime.now(),
    }
    return signal, f"{path_label}: SIGNAL {direction} @ {entry_price}"


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    signals = []

    sig, msg = _process_path("Swing (Daily)", daily_df, h4_df, m15_df, "Swing (Daily)", m15_df)
    log(msg)
    if sig:
        signals.append(sig)

    sig, msg = _process_path("Swing (H4)", h4_df, h1_df, m15_df, "Swing (H4)", m15_df)
    log(msg)
    if sig:
        signals.append(sig)

    if m5_df is not None:
        sig, msg = _process_path("Scalp (H1)", h1_df, m15_df, m5_df, "Scalp (H1)", m5_df)
        log(msg)
        if sig:
            signals.append(sig)

    if not signals:
        log("No trade opportunity found on any path (Swing-Daily, Swing-H4, Scalp)")

    return signals


def analyze_market(debug: bool = False) -> list:
    daily_df = dc.get_candles("D1", count=120)
    h4_df = dc.get_candles("H4", count=120)
    h1_df = dc.get_candles("H1", count=150)
    m15_df = dc.get_candles("M15", count=150)
    m5_df = dc.get_candles("M5", count=100)

    return analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug=debug)
