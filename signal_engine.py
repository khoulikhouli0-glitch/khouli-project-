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


def _get_bias(bias_df):
    sw = smc.swing_highs_lows(bias_df, swing_length=BIAS_SWING_LENGTH)
    bc = smc.bos_choch(bias_df, sw, close_break=True)

    for i in range(len(bc) - 1, -1, -1):
        bos = bc["BOS"].iloc[i]
        choch = bc["CHOCH"].iloc[i]
        if pd.notna(bos) and bos != 0:
            return "bullish" if bos == 1 else "bearish"
        if pd.notna(choch) and choch != 0:
            return "bullish" if choch == 1 else "bearish"

    return None


def _premium_discount_zone(zone_df, lookback=PD_LOOKBACK):
    recent = zone_df.tail(lookback)
    range_high = float(recent["high"].max())
    range_low = float(recent["low"].min())
    midpoint = (range_high + range_low) / 2
    current_price = float(zone_df["close"].iloc[-1])
    return "premium" if current_price > midpoint else "discount"


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

        level = bc["Level"].iloc[i]
        broken_idx = bc["BrokenIndex"].iloc[i]
        confirm_index = int(broken_idx) if pd.notna(broken_idx) else i

        if is_bos:
            return {
                "event": "BOS (continuation)",
                "confirm_index": confirm_index,
                "stop_ref": float(level),
                "stop_source": "Behind broken structure level",
            }

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

        swept_level = float(swept.iloc[-1]["Level"])
        return {
            "event": "Sweep then CHoCH",
            "confirm_index": confirm_index,
            "stop_ref": swept_level,
            "stop_source": "Behind swept liquidity level",
        }

    return None


def _liquidity_target(zone_df, sw, liq, direction, current_price, daily_df):
    wanted = 1 if direction == "bullish" else -1
    unswept = liq[(liq["Liquidity"] == wanted) & (liq["Swept"].isna())]

    if not unswept.empty:
        if direction == "bullish":
            candidates = unswept[unswept["Level"] > current_price]
            if not candidates.empty:
                return float(candidates["Level"].min()), "Equal Highs (unswept liquidity)"
        else:
            candidates = unswept[unswept["Level"] < current_price]
            if not candidates.empty:
                return float(candidates["Level"].max()), "Equal Lows (unswept liquidity)"

    if daily_df is not None and len(daily_df) >= 2:
        prev_day = daily_df.iloc[-2]
        if direction == "bullish" and float(prev_day["high"]) > current_price:
            return float(prev_day["high"]), "Previous Day High (PDH)"
        if direction == "bearish" and float(prev_day["low"]) < current_price:
            return float(prev_day["low"]), "Previous Day Low (PDL)"

    highs = sw[sw["HighLow"] == 1]["Level"]
    lows = sw[sw["HighLow"] == -1]["Level"]

    if direction == "bullish":
        candidates = highs[highs > current_price]
        if candidates.empty:
            return None, None
        return float(candidates.min()), "Nearest swing high"

    candidates = lows[lows < current_price]
    if candidates.empty:
        return None, None
    return float(candidates.max()), "Nearest swing low"


def _find_entry(ob, fvg, ret, direction, current_price):
    wanted = 1 if direction == "bullish" else -1

    ob_matches = ob[ob["OB"] == wanted]
    for i in range(len(ob_matches) - 1, -1, -1):
        row = ob_matches.iloc[i]
        top, bottom = float(row["Top"]), float(row["Bottom"])
        if bottom * (1 - ENTRY_TOLERANCE) <= current_price <= top * (1 + ENTRY_TOLERANCE):
            return "Order Block", {"top": top, "bottom": bottom}

    fvg_matches = fvg[(fvg["FVG"] == wanted) & (fvg["MitigatedIndex"].isna())]
    for i in range(len(fvg_matches) - 1, -1, -1):
        row = fvg_matches.iloc[i]
        top, bottom = float(row["Top"]), float(row["Bottom"])
        if bottom * (1 - ENTRY_TOLERANCE) <= current_price <= top * (1 + ENTRY_TOLERANCE):
            return "Fair Value Gap", {"top": top, "bottom": bottom}

    if len(ret) > 0:
        last = ret.iloc[-1]
        current_retracement = last["CurrentRetracement%"]
        ret_direction = last["Direction"]
        if pd.notna(current_retracement) and ret_direction == wanted:
            if 61.8 <= abs(current_retracement) <= 79:
                return "OTE (61.8%-79% retracement)", {
                    "top": current_price * (1 + ENTRY_TOLERANCE),
                    "bottom": current_price * (1 - ENTRY_TOLERANCE),
                }

    return None, None


def _build_reason(path_label, direction_word, liquidity_source, confirm_event,
                   entry_array_name, entry_zone, stop_source, target_price, pd_zone):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word}\n"
        f"Premium/Discount: {pd_zone}\n"
        f"Liquidity target: {liquidity_source} @ {round(target_price, 2)}\n"
        f"Trigger: {confirm_event}\n"
        f"Entry array: {entry_array_name}\n"
        f"Entry range: {round(entry_zone['bottom'], 2)} - {round(entry_zone['top'], 2)}\n"
        f"Stop basis: {stop_source}"
    )


def _process_path(bias_df, zone_df, entry_df, path_label, daily_df=None):
    direction_word = _get_bias(bias_df)
    if direction_word is None:
        return None, f"{path_label}: skipped - no BOS/CHoCH bias found"

    pd_zone = _premium_discount_zone(zone_df)
    wanted_zone = "discount" if direction_word == "bullish" else "premium"
    if pd_zone != wanted_zone:
        return None, f"{path_label}: skipped - price in {pd_zone} zone, need {wanted_zone}"

    sw, bc, liq, ob, fvg, ret = _analyze_zone(zone_df)

    trigger = _find_trigger(zone_df, bc, liq, direction_word, CONFIRM_MAX_WAIT)
    if trigger is None:
        return None, f"{path_label}: skipped - no matching BOS/CHoCH trigger on zone timeframe"

    zone_current_price = float(zone_df["close"].iloc[-1])
    liquidity_price, liquidity_source = _liquidity_target(
        zone_df, sw, liq, direction_word, zone_current_price, daily_df
    )
    if liquidity_price is None:
        return None, f"{path_label}: skipped - no liquidity target found"

    current_price = float(entry_df["close"].iloc[-1])
    entry_array_name, entry_zone = _find_entry(ob, fvg, ret, direction_word, current_price)
    if entry_zone is None:
        return None, f"{path_label}: skipped - price not in any entry array (OB/FVG/OTE)"

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
        path_label, direction_word, liquidity_source, trigger["event"],
        entry_array_name, entry_zone, trigger["stop_source"], take_profit, pd_zone,
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
        "confidence_label": "منهج SMC عبر مكتبة smartmoneyconcepts (multi-timeframe)",
        "timestamp": datetime.now(),
    }
    return signal, f"{path_label}: SIGNAL {direction} @ {entry_price} via {entry_array_name} ({trigger['event']})"


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    signals = []

    sig, msg = _process_path(daily_df, h4_df, m15_df, "Swing (Daily)", daily_df=None)
    log(msg)
    if sig:
        signals.append(sig)

    sig, msg = _process_path(h4_df, h1_df, m15_df, "Swing (H4)", daily_df=daily_df)
    log(msg)
    if sig:
        signals.append(sig)

    if m5_df is not None:
        sig, msg = _process_path(h1_df, m15_df, m5_df, "Scalp (H1)", daily_df=daily_df)
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
    
