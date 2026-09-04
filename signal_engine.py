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
DISPLACEMENT_STD_LOOKBACK = 20
DISPLACEMENT_STD_MULT = 3.0
LONDON_KILLZONE = (7, 10)
NY_KILLZONE = (12, 15)


def _level_still_respected(df, level_idx, level, direction):
    after = df.iloc[level_idx + 1:]
    if len(after) == 0:
        return True
    if direction == "bullish":
        violated = (after["close"] < level).any()
    else:
        violated = (after["close"] > level).any()
    return not violated


def _in_killzone(candle_time) -> bool:
    hour = candle_time.hour
    in_london = LONDON_KILLZONE[0] <= hour < LONDON_KILLZONE[1]
    in_ny = NY_KILLZONE[0] <= hour < NY_KILLZONE[1]
    return in_london or in_ny


def _measure_displacement(df, start_idx, end_idx):
    closes = df["close"]
    pct_changes = closes.pct_change().dropna()
    window = pct_changes.iloc[max(0, start_idx - DISPLACEMENT_STD_LOOKBACK): start_idx]
    if len(window) < 5:
        return False, 0.0

    std = window.std()
    if std == 0 or pd.isna(std):
        return False, 0.0

    start_price = float(closes.iloc[start_idx])
    end_price = float(closes.iloc[end_idx])
    move_pct = abs(end_price - start_price) / start_price
    strength = move_pct / std if std > 0 else 0.0

    return strength >= DISPLACEMENT_STD_MULT, round(strength, 2)


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
            disp_start = max(0, i - 3)
            disp_ok, disp_strength = _measure_displacement(zone_df, disp_start, confirm_index)
            if not disp_ok:
                continue
            return {
                "event": "BOS (continuation)",
                "confirm_index": confirm_index,
                "displacement_strength": disp_strength,
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

        sweep_index = int(swept.iloc[-1]["Swept"])
        disp_ok, disp_strength = _measure_displacement(zone_df, sweep_index, confirm_index)
        if not disp_ok:
            continue

        return {
            "event": "Sweep then CHoCH",
            "confirm_index": confirm_index,
            "displacement_strength": disp_strength,
        }

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
                   entry_array_name, entry_zone, target_price, pd_zone, broken_level, stop_loss,
                   break_age, displacement_strength, killzone_note):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word} (on {bias_label})\n"
        f"Premium/Discount: {pd_zone}\n"
        f"Liquidity target: {liquidity_source} @ {round(target_price, 2)}\n"
        f"Trigger: {confirm_event} (displacement {displacement_strength}x std)\n"
        f"Timing: {killzone_note}\n"
        f"Entry array: {entry_array_name}\n"
        f"Entry zone: {round(entry_zone['bottom'], 2)} - {round(entry_zone['top'], 2)}\n"
        f"Broken structure level ({bias_label}, {break_age} candles ago, still respected): {round(broken_level, 2)}\n"
        f"Stop basis: Behind that level, at {round(stop_loss, 2)}"
    )


def _process_path(bias_df, bias_label, zone_df, entry_df, path_label, daily_df=None):
    direction_word, sw_bias, bc_bias, bias_level, bias_event, break_age = _get_bias(bias_df)
    if direction_word is None:
        return None, f"{path_label}: skipped - no respected BOS/CHoCH bias found"

    pd_zone = _premium_discount_zone(zone_df)
    wanted_zone = "discount" if direction_word == "bullish" else "premium"
    if pd_zone != wanted_zone:
        return None, f"{path_label}: skipped - price in {pd_zone} zone, need {wanted_zone}"

    sw, bc, liq, ob, fvg, ret = _analyze_zone(zone_df)

    trigger = _find_trigger(zone_df, bc, liq, direction_word, CONFIRM_MAX_WAIT)
    if trigger is None:
        return None, f"{path_label}: skipped - no respected trigger with sufficient displacement"

    bias_current_price = float(bias_df["close"].iloc[-1])
    liquidity_price, liquidity_source = _liquidity_target(
        bias_df, sw_bias, direction_word, bias_current_price, daily_df
    )
    if liquidity_price is None:
        return None, f"{path_label}: skipped - no liquidity target found"

    current_price = float(entry_df["close"].iloc[-1])
    entry_array_name, entry_zone, entry_price = _find_entry(ob, fvg, ret, direction_word, current_price)
    if entry_zone is None:
        return None, f"{path_label}: skipped - price not in any entry array (OB/FVG/OTE)"

    direction = "BUY" if direction_word == "bullish" else "SELL"

    if direction == "BUY":
        stop_loss = bias_level * 0.9993
    else:
        stop_loss = bias_level * 1.0007

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

    entry_time = entry_df["time"].iloc[-1]
    in_kz = _in_killzone(entry_time)
    killzone_note = "Inside London/NY killzone" if in_kz else "Outside killzone (still valid)"

    if daily_df is not None:
        pass

    confidence = "high" if in_kz else "standard"
    confidence_label = (
        "منهج SMC كامل - داخل جلسة لندن/نيويورك" if in_kz
        else "منهج SMC كامل - خارج جلسة لندن/نيويورك (لا يزال صالحًا)"
    )

    reason = _build_reason(
        path_label, bias_label, direction_word, liquidity_source, trigger["event"],
        entry_array_name, entry_zone, take_profit, pd_zone, bias_level, stop_loss,
        break_age, trigger["displacement_strength"], killzone_note,
    )

    signal = {
        "symbol": Config.SYMBOL,
        "direction": direction,
        "entry": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "reason": reason,
        "trade_label": path_label,
        "confidence": confidence,
        "confidence_label": confidence_label,
        "timestamp": datetime.now(),
    }
    return signal, f"{path_label}: SIGNAL {direction} @ {entry_price} via {entry_array_name} ({trigger['event']}, {killzone_note})"


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    signals = []

    sig, msg = _process_path(daily_df, "Daily", h4_df, m15_df, "Swing (Daily)", daily_df=None)
    log(msg)
    if sig:
        signals.append(sig)

    sig, msg = _process_path(h4_df, "H4", h1_df, m15_df, "Swing (H4)", daily_df=daily_df)
    log(msg)
    if sig:
        signals.append(sig)

    if m5_df is not None:
        sig, msg = _process_path(h1_df, "H1", m15_df, m5_df, "Scalp (H1)", daily_df=daily_df)
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
