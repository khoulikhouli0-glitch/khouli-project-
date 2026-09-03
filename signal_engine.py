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


def _get_pdh_pdl(daily_df, direction, current_price):
    if daily_df is None or len(daily_df) < 2:
        return None, None

    prev_day = daily_df.iloc[-2]

    if direction == "bullish":
        level = float(prev_day["high"])
        if level > current_price:
            return level, "Previous Day High (PDH)"
        return None, None

    level = float(prev_day["low"])
    if level < current_price:
        return level, "Previous Day Low (PDL)"
    return None, None


def _find_liquidity_target(df, direction, daily_df=None):
    equal_level = smc.find_equal_levels(df, direction)
    if equal_level is not None:
        return equal_level, "Equal Highs/Lows (liquidity pool)"

    current_price = float(df["close"].iloc[-1])

    if daily_df is not None:
        pdh_pdl_level, pdh_pdl_label = _get_pdh_pdl(daily_df, direction, current_price)
        if pdh_pdl_level is not None:
            return pdh_pdl_level, pdh_pdl_label

    swings = smc.find_swing_points(df)

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


def _last_n_swings(swings_df, up_to_index, n=4):
    window = swings_df.iloc[: up_to_index + 1]
    highs = window[window["is_swing_high"]][["time", "high"]].tail(n)
    lows = window[window["is_swing_low"]][["time", "low"]].tail(n)
    return highs, lows


def _structure_at(df, swings_df, up_to_index):
    highs, lows = _last_n_swings(swings_df, up_to_index)
    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "unclear", "last_event": None, "level": None}

    last_close = df["close"].iloc[up_to_index]
    last_high = highs["high"].iloc[-1]
    prev_high = highs["high"].iloc[-2]
    last_low = lows["low"].iloc[-1]
    prev_low = lows["low"].iloc[-2]

    bullish_structure = last_high > prev_high and last_low > prev_low
    bearish_structure = last_high < prev_high and last_low < prev_low

    trend = "unclear"
    last_event = None
    level = None

    if bullish_structure:
        trend = "bullish"
        if last_close > last_high:
            last_event = "BOS"
            level = last_high
    elif bearish_structure:
        trend = "bearish"
        if last_close < last_low:
            last_event = "BOS"
            level = last_low
    else:
        if last_close > last_high:
            trend = "bullish"
            last_event = "CHoCH"
            level = last_high
        elif last_close < last_low:
            trend = "bearish"
            last_event = "CHoCH"
            level = last_low

    return {"trend": trend, "last_event": last_event, "level": level}


def _find_reversal_entry(df, swings_df, direction, lookback, max_wait):
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
            structure = _structure_at(df, swings_df, j)
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


def _find_continuation_entry(df, swings_df, direction, lookback):
    n = len(df)
    start = max(1, n - lookback)

    for i in range(n - 1, start - 1, -1):
        structure = _structure_at(df, swings_df, i)
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


def _process_path(bias_direction_df, sweep_confirm_df, entry_df, path_label, daily_df=None):
    direction_word = _get_bias(bias_direction_df)
    if direction_word not in ("bullish", "bearish"):
        return None, f"{path_label}: skipped - bias unclear on source timeframe"

    liquidity_price, liquidity_source = _find_liquidity_target(bias_direction_df, direction_word, daily_df)
    if liquidity_price is None:
        return None, f"{path_label}: skipped - no liquidity target found"

    swings_df = smc.find_swing_points(sweep_confirm_df)

    trigger = _find_reversal_entry(sweep_confirm_df, swings_df, direction_word, SWEEP_LOOKBACK, CONFIRM_MAX_WAIT)
    if trigger is None:
        trigger = _find_continuation_entry(sweep_confirm_df, swings_df, direction_word, SWEEP_LOOKBACK)
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
        "symbol": Config
