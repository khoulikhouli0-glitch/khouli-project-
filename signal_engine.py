from datetime import datetime

import pandas as pd
from smartmoneyconcepts import smc

import deriv_connector as dc
from config import Config

BIAS_SWING_LENGTH = 5
ZONE_SWING_LENGTH = 3
LIQUIDITY_RANGE_PCT = 0.01
CONFIRM_MAX_WAIT = 6
MIN_RR = 1.5
ENTRY_TOLERANCE = 0.0008
PD_LOOKBACK = 50


def _level_still_respected(df, level_idx, level, direction):
    after = df.iloc[level_idx + 1:]
    if len(after) == 0:
        return True
    if direction == "bullish":
        violated = (after["close"] < level).any()
    else:
        violated = (after["close"] > level).any()
    return not violated


def _get_bias(bias_df):
    sw = smc.swing_highs_lows(bias_df, swing_length=BIAS_SWING_LENGTH)
    bc = smc.bos_choch(bias_df, sw, close_break=True)
    n = len(bc)

    for i in range(n - 1, -1, -1):
        bos = bc["BOS"].iloc[i]
        choch = bc["CHOCH"].iloc[i]

        is_bos = pd.notna(bos) and bos != 0
        is_choch = pd.notna(choch) and choch != 0
        if not (is_bos or is_choch):
            continue

        level = float(bc["Level"].iloc[i])
        direction = "bullish" if (bos == 1 if is_bos else choch == 1) else "bearish"

        if not _level_still_respected(bias_df, i, level, direction):
            continue

        event = "BOS" if is_bos else "CHoCH"
        age = (n - 1) - i
        return direction, sw, bc, level, event, age

    return None, sw, bc, None, None, None


def _premium_discount_zone(zone_df, lookback=PD_LOOKBACK):
    recent = zone_df.tail(lookback)
    range_high = float(recent["high"].max())
    range_low = float(recent["low"].min())
    midpoint = (range_high + range_low) / 2
    current_price = float(zone_df["close"].iloc[-1])
    return "premium" if current_price > midpoint else "discount"


def _liquidity_target(bias_df, sw_bias, direction, current_price, daily_df):
    liq = smc.liquidity(bias_df, sw_bias, range_percent=LIQUIDITY_RANGE_PCT)
    wanted = 1 if direction == "bullish" else -1
    unswept = liq[(liq["Liquidity"] == wanted) & (liq["Swept"].isna())]

    candidates = []

    if direction == "bullish":
        eq = unswept[unswept["Level"] > current_price]
        if not eq.empty:
            candidates.append((float(eq["Level"].min()), "Equal Highs (unswept liquidity, H-frame)"))
    else:
        eq = unswept[unswept["Level"] < current_price]
        if not eq.empty:
            candidates.append((float(eq["Level"].max()), "Equal Lows (unswept liquidity, H-frame)"))

    if daily_df is not None and len(daily_df) >= 2:
        prev_day = daily_df.iloc[-2]
        if direction == "bullish" and float(prev_day["high"]) > current_price:
            candidates.append((float(prev_day["high"]), "Previous Day High (PDH)"))
        if direction == "bearish" and float(prev_day["low"]) < current_price:
            candidates.append((float(prev_day["low"]), "Previous Day Low (PDL)"))

    if not candidates:
        highs = sw_bias[sw_bias["HighLow"] == 1]["Level"]
        lows = sw_bias[sw_bias["HighLow"] == -1]["Level"]
        if direction == "bullish":
            far = highs[highs > current_price]
            if not far.empty:
                candidates.append((float(far.min()), "Nearest significant swing high (H-frame)"))
        else:
            far = lows[lows < current_price]
            if not far.empty:
                candidates.append((float(far.max()), "Nearest significant swing low (H-frame)"))

    if not candidates:
        return None, None

    if direction == "bullish":
        return min(candidates, key=lambda c: c[0])
    return max(candidates, key=lambda c: c[0])


def _analyze_zone(zone_df):
    sw = smc.swing_highs_lows(zone_df, swing_length=ZONE_SWING_LENGTH)
    bc = smc.bos_choch(zone_df, sw, close_break=True)
    liq = smc.liquidity(zone_df, sw, range_percent=LIQUIDITY_RANGE_PCT)
    ob = smc.ob(zone_df, sw, close_mitigation=False)
    fvg = smc.fvg(zone_df, join_consecutive=True)
    ret = smc.retracements(zone_df, sw)
    return sw, bc, liq, ob, fvg, ret


def _find_trigger(zone_df, bc, liq, direction, max_wait):
    wanted = 1 if direction == "bullish" else -1
    n = len(bc)

    for i in range(n - 1, -1, -1):
        bos = bc["BOS"].iloc[i]
        choch = bc["CHOCH"].iloc[i]

        is_bos = pd.notna(bos) and bos == wanted
        is_choch = pd.notna(choch) and choch == wanted
        if not (is_bos or is_choch):
            continue

        level = float(bc["Level"].iloc[i])
        if not _level_still_respected(zone_df, i, level, direction):
            continue

        broken_idx = bc["BrokenIndex"].iloc[i]
        confirm_index = int(broken_idx) if pd.notna(broken_idx) else i

        if is_bos:
            return {"event": "BOS (continuation)", "confirm_index": confirm_index}

        opposite_liq = -wanted
        window_start = max(0, confirm_index - max_wait)
        swept = liq[
            (liq["Liquidity"] == opposite_liq)
            & (liq["Swept"].notna())
            & (liq["Swept"] >= window_start)
            & (liq["Swept"] <= confirm_index)
        ]
        if swept.empty:
            continue

        return {"event": "Sweep then CHoCH", "confirm_index": confirm_index}

    return None


def _find_entry(ob, fvg, ret, direction, current_price):
    wanted = 1 if direction == "bullish" else -1

    ob_matches = ob[ob["OB"] == wanted]
    for i in range(len(ob_matches) - 1, -1, -1):
        row = ob_matches.iloc[i]
        top, bottom = float(row["Top"]), float(row["Bottom"])
        if bottom * (1 - ENTRY_TOLERANCE) <= current_price <= top * (1 + ENTRY_TOLERANCE):
            entry_price = top if direction == "bullish" else bottom
            return "Order Block", {"top": top, "bottom": bottom}, entry_price

    fvg_matches = fvg[(fvg["FVG"] == wanted) & (fvg["MitigatedIndex"].isna())]
    for i in range(len(fvg_matches) - 1, -1, -1):
        row = fvg_matches.iloc[i]
        top, bottom = float(row["Top"]), float(row["Bottom"])
        if bottom * (1 - ENTRY_TOLERANCE) <= current_price <= top * (1 + ENTRY_TOLERANCE):
            entry_price = top if direction == "bullish" else bottom
            return "Fair Value Gap", {"top": top, "bottom": bottom}, entry_price

    if len(ret) > 0:
        last = ret.iloc[-1]
        current_retracement = last["CurrentRetracement%"]
        ret_direction = last["Direction"]
        if pd.notna(current_retracement) and ret_direction == wanted:
            if 61.8 <= abs(current_retracement) <= 79:
                zone = {
                    "top": current_price * (1 + ENTRY_TOLERANCE),
                    "bottom": current_price * (1 - ENTRY_TOLERANCE),
                }
                return "OTE (61.8%-79% retracement)", zone, current_price

    return None, None, None


def _build_reason(path_label, bias_label, direction_word, liquidity_source, confirm_event,
                   entry_array_name, entry_zone, target_price, pd_zone, broken_level, stop_loss, break_age):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word} (on {bias_label})\n"
        f"Premium/Discount: {pd_zone}\n"
        f"Liquidity target: {liquidity_source} @ {round(target_price, 2)}\n"
        f"Trigger: {confirm_event}\n"
        f"Entry array: {entry_array_name}\n"
        f"Entry zone: {round(entry_zone['bottom'], 2)} - {round(entry_zone['top'], 2)}\n"
        f"Broken structure level ({bias_label}, {break_age} candles ago, still respected): {round(broken_level, 2)}\n"
