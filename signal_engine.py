from datetime import datetime

import deriv_connector as dc
import smc_analysis as smc
import zone_analysis as zones
import wave_analysis as wave
from config import Config

SWEEP_LOOKBACK = 40
CONFIRM_MAX_WAIT = 6
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


def _find_reversal_entry(df, direction, lookback, max_wait):
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
            confirm_df = df.iloc[: j + 1]
            structure = smc.detect_structure(confirm_df)
            if structure["last_event"] == "CHoCH" and structure["trend"] == direction:
                impulse_start = float(df.iloc[i]["low"] if direction == "bullish" else df.iloc[i]["high"])
                segment = df.iloc[i: j + 1]
                impulse_end = float(segment["high"].max() if direction == "bullish" else segment["low"].min())
                stop_ref = float(df.iloc[i]["low"] if direction == "bullish" else df.iloc[i]["high"])
                return {
                    "confirm_index": j,
                    "confirm_event": "Sweep then CHoCH",
                    "impulse_start": impulse_start,
                    "impulse_end": impulse_end,
                    "stop_ref": stop_ref,
                    "stop_source": "Behind sweep extreme",
                }

    return None


def _find_continuation_entry(df, direction, lookback):
    n = len(df)
    start = max(1, n - lookback)

    for i in range(n - 1, start - 1, -1):
        sub_df = df.iloc[: i + 1]
        structure = smc.detect_structure(sub_df)
        if structure["last_event"] == "BOS" and structure["trend"] == direction:
            level = float(structure["level"])
            recent = df.iloc[max(0, i - 3): i + 1]
            impulse_end = float(recent["high"].max() if direction == "bullish" else recent["low"].min())
            return {
                "confirm_index": i,
                "confirm_event": "BOS (continuation)",
                "impulse_start": level,
                "impulse_end": impulse_end,
                "stop_ref": level,
                "stop_source": "Behind broken structure level",
            }

    return None


def _find_pd_array(df, direction):
    checks = [
        ("Order Block", smc.find_last_order_block(df, direction=direction)),
        ("Breaker Block", smc.find_breaker_block(df, direction=direction)),
        ("Mitigation Block", smc.find_mitigation_block(df, direction=direction)),
        ("Inversion FVG", smc.find_inversion_fvg(df, direction=direction)),
    ]
    return [(name, z) for name, z in checks if z is not None]


def _find_entry_match(pd_candidates, ote_zone, current_price):
    all_candidates = list(pd_candidates) + [("OTE (61.8%-79%)", ote_zone)]
    for name, zone in all_candidates:
        top = zone["top"] * 1.0008
        bottom = zone["bottom"] * 0.9992
        if bottom <= current_price <= top:
            return name, zone
    return None, None


def _build_reason(path_label, direction_word, liquidity_source, confirm_event,
                   entry_array_name, entry_zone, stop_source, target_price):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word}\n"
        f"Liquidity target: {liquidity_source} @ {round(target_price, 2)}\n"
        f"Trigger: {confirm_event}\n"
        f"Entry array: {entry_array_name}\n"
        f"Entry range: {round(entry_zone['bottom'], 2)} - {round(entry_zone['top'], 2)}\n"
        f"Stop basis: {stop_source}"
    )


def _process_path(bias_direction_df, sweep_confirm_df, entry_df, path_label):
    direction_word = _get_bias(bias_direction_df)
    if direction_word not in ("bullish", "bearish"):
        return None, f"{path_label}: skipped - bias unclear on source timeframe"

    liquidity_price, liquidity_source = _find_liquidity_target(bias_direction_df, direction_word)
    if liquidity_price is None:
        return None, f"{path_label}: skipped - no liquidity target found"

    trigger = _find_reversal_entry(sweep_confirm_df, direction_word, SWEEP_LOOKBACK, CONFIRM_MAX_WAIT)
    if trigger is None:
        trigger = _find_continuation_entry(sweep_confirm_df, direction_word, SWEEP_LOOKBACK)
    if trigger is None:
        return None, f"{path_label}: skipped - no BOS/CHoCH trigger found"

    impulse = {
        "start_price": trigger["impulse_start"],
        "end_price": trigger["impulse_end"],
        "direction": "up" if direction_word == "bullish" else "down",
    }
    ote_zone = wave.fibonacci_zone(impulse)

    pd_candidates = _find_pd_array(sweep_confirm_df.iloc[: trigger["confirm_index"] + 1], direction_word)

    current_price = float(entry_df["close"].iloc[-1])
    entry_array_name, entry_zone = _find_entry_match(pd_candidates, ote_zone, current_price)
    if entry_zone is None:
        return None, f"{path_label}: skipped - price not yet in any entry array (PD/OTE)"

    direction = "BUY" if direction_word == "bullish" else "SELL"
    entry_price = current_price

    if direction == "BUY":
        stop_loss = trigger["stop_ref"] * 0.9993
    else:
        stop_loss = trigger["stop_ref"] * 1.0007

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

    reason = _build_reason(
        path_label, direction_word, liquidity_source, trigger["confirm_event"],
        entry_array_name, entry_zone, trigger["stop_source"], take_profit,
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
        "confidence_label": "منهج SMC: تحيّز + سيولة + (سحب+CHoCH أو BOS) + PD Array/OTE",
        "timestamp": datetime.now(),
    }
    return signal, f"{path_label}: SIGNAL {direction} @ {entry_price} via {entry_array_name} ({trigger['confirm_event']})"


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    signals = []

    sig, msg = _process_path(daily_df, h4_df, m15_df, "Swing (Daily)")
    log(msg)
    if sig:
        signals.append(sig)

    sig, msg = _process_path(h4_df, h1_df, m15_df, "Swing (H4)")
    log(msg)
    if sig:
        signals.append(sig)

    if m5_df is not None:
        sig, msg = _process_path(h1_df, m15_df, m5_df, "Scalp (H1)")
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
