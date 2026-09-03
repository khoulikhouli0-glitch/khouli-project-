from datetime import datetime

import deriv_connector as dc
import smc_analysis as smc
import zone_analysis as zones
import confirmation_analysis as confirm
from config import Config

TOUCH_LOOKBACK = 40
INTERMEDIATE_WINDOW_CANDLES = 6
SWING_LEFT_RIGHT = 3
MIN_SWING_RR = 1.5


def _get_bias(df) -> str:
    structure = smc.detect_structure(df)
    return structure["trend"]


def _find_intermediate_confirmation(df, zones_list, direction, lookback, max_wait):
    n = len(df)
    if n == 0 or not zones_list:
        return None

    start = max(1, n - lookback)
    in_zone = False
    result = None

    for i in range(start, n):
        candle = df.iloc[i]
        zone = zones.price_in_any_zone(candle["close"], zones_list)
        if zone is None:
            zone = zones.price_in_any_zone(candle["low"], zones_list)
        if zone is None:
            zone = zones.price_in_any_zone(candle["high"], zones_list)

        touched_now = zone is not None

        if touched_now and not in_zone:
            confirm_end = min(n, i + max_wait)
            for j in range(i, confirm_end):
                sub_df = df.iloc[: j + 1]
                structure = smc.detect_structure(sub_df)
                sweep = smc.detect_liquidity_sweep(sub_df, check_last=1)

                struct_ok = (
                    structure["last_event"] in ("BOS", "CHoCH")
                    and structure["trend"] == direction
                )
                sweep_ok = sweep["swept"] and (
                    (direction == "bullish" and sweep["direction"] == "buy_side_taken")
                    or (direction == "bearish" and sweep["direction"] == "sell_side_taken")
                )

                if struct_ok or sweep_ok:
                    confirm_type = "Structure Shift (BOS/CHoCH)" if struct_ok else "Liquidity Sweep"
                    result = (zone, i, j, confirm_type)
                    break

        in_zone = touched_now

    return result


def _find_structural_stop(df, direction, up_to_index):
    sub_df = df.iloc[: up_to_index + 1]
    if len(sub_df) < (SWING_LEFT_RIGHT * 2 + 3):
        return None

    swings = smc.find_swing_points(sub_df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT)

    if direction == "bullish":
        lows = swings[swings["is_swing_low"]]["low"]
        if lows.empty:
            return None
        return float(lows.iloc[-1])

    highs = swings[swings["is_swing_high"]]["high"]
    if highs.empty:
        return None
    return float(highs.iloc[-1])


def _find_liquidity_target(source_df, direction, entry_price):
    swings = smc.find_swing_points(source_df, left=SWING_LEFT_RIGHT, right=SWING_LEFT_RIGHT)

    if direction == "bullish":
        candidates = swings[swings["is_swing_high"]]["high"]
        candidates = candidates[candidates > entry_price]
        if candidates.empty:
            return None
        return float(candidates.min())

    candidates = swings[swings["is_swing_low"]]["low"]
    candidates = candidates[candidates < entry_price]
    if candidates.empty:
        return None
    return float(candidates.max())


def _zone_description(matched_zone) -> str:
    source = matched_zone["source"]
    if source == "Fibonacci":
        return "Fibonacci (OTE 61.8%-79%)"
    return source


def _build_reason(path_label, direction_word, matched_zone, intermediate_label,
                   intermediate_type, entry_label, entry_confirmation, confidence_label,
                   stop_source, target_source):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word}\n"
        f"Zone of interest: {_zone_description(matched_zone)}\n"
        f"Intermediate confirmation on {intermediate_label}: {intermediate_type}\n"
        f"Entry confirmation on {entry_label}: {entry_confirmation}\n"
        f"Stop basis: {stop_source}\n"
        f"Target basis: {target_source}\n"
        f"Confidence: {confidence_label}"
    )


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    daily_bias = _get_bias(daily_df)
    h4_bias = _get_bias(h4_df)
    h1_bias = _get_bias(h1_df)
    m15_bias = _get_bias(m15_df)
    log(f"Daily bias={daily_bias}  H4 bias={h4_bias}  H1 bias={h1_bias}  M15 bias={m15_bias}")

    daily_direction = None
    if daily_bias == "bullish":
        daily_direction = "BUY"
    elif daily_bias == "bearish":
        daily_direction = "SELL"

    paths = []

    if daily_bias in ("bullish", "bearish"):
        daily_zones_dir = zones.collect_zones(daily_df, direction=daily_bias)
        h4_zones_for_daily = zones.collect_zones(h4_df, direction=daily_bias)
        confluence_daily = zones.find_confluence(daily_zones_dir, h4_zones_for_daily, "Daily", "H4")
        paths.append({
            "label": "Swing (Daily)",
            "bias": daily_bias,
            "zones_list": confluence_daily,
            "source_df": daily_df,
            "intermediate_df": h4_df,
            "intermediate_label": "H4",
            "entry_df": m15_df,
            "entry_label": "M15",
            "swing_style": True,
            "scalp_style": False,
        })

    if h4_bias in ("bullish", "bearish"):
        h4_zones_dir = zones.collect_zones(h4_df, direction=h4_bias)
        h1_zones_for_h4 = zones.collect_zones(h1_df, direction=h4_bias)
        confluence_h4 = zones.find_confluence(h4_zones_dir, h1_zones_for_h4, "H4", "H1")
        paths.append({
            "label": "Swing (H4)",
            "bias": h4_bias,
            "zones_list": confluence_h4,
            "source_df": h4_df,
            "intermediate_df": h1_df,
            "intermediate_label": "H1",
            "entry_df": m15_df,
            "entry_label": "M15",
            "swing_style": True,
            "scalp_style": False,
        })

    h1_conflicts_m15 = (
        m15_bias in ("bullish", "bearish")
        and h1_bias in ("bullish", "bearish")
        and h1_bias != m15_bias
    )
    if m15_bias in ("bullish", "bearish") and m5_df is not None and not h1_conflicts_m15:
        scalp_zones = zones.collect_zones(h1_df, direction=m15_bias)
        paths.append({
            "label": "Scalp (H1)",
            "bias": m15_bias,
            "zones_list": scalp_zones,
            "source_df": h1_df,
            "intermediate_df": m15_df,
            "intermediate_label": "M15",
            "entry_df": m5_df,
            "entry_label": "M5",
            "swing_style": False,
            "scalp_style": True,
            "target_multiplier": 1.5,
        })
    elif h1_conflicts_m15:
        log(f"Scalp (H1): skipped - M15 bias ({m15_bias}) conflicts with H1 bias ({h1_bias})")

    signals = []

    for path in paths:
        direction_word = path["bias"]
        zones_list = path["zones_list"]
        if not zones_list:
            log(f"{path['label']}: no confluence/zones of interest")
            continue

        if path["swing_style"]:
            pd_zone = smc.get_premium_discount_zone(path["source_df"])
            wanted_zone = "discount" if direction_word == "bullish" else "premium"
            if pd_zone["zone"] != wanted_zone:
                log(f"{path['label']}: skipped - price in {pd_zone['zone']} zone, need {wanted_zone}")
                continue

        found = _find_intermediate_confirmation(
            path["intermediate_df"],
            zones_list,
            direction_word,
            lookback=TOUCH_LOOKBACK,
            max_wait=INTERMEDIATE_WINDOW_CANDLES,
        )
        if found is None:
            log(f"{path['label']}: no fresh confirmed touch on {path['intermediate_label']}")
            continue

        matched_zone, touch_idx, confirm_idx, intermediate_type = found
        log(f"{path['label']}: touch+confirm on {path['intermediate_label']} ({intermediate_type})")

        entry_confirmation = confirm.get_confirmation(path["entry_df"], direction=direction_word, lookback=10)
        log(f"{path['label']}: entry confirmation on {path['entry_label']} = {entry_confirmation}")
        if entry_confirmation is None:
            continue

        direction = "BUY" if direction_word == "bullish" else "SELL"
        entry_price = float(path["entry_df"]["close"].iloc[-1])

        stop_source = "Zone edge"
        stop_loss = None

        if path["scalp_style"]:
            structural_level = _find_structural_stop(
                path["intermediate_df"], direction_word, confirm_idx
            )
            if structural_level is not None:
                if direction == "BUY":
                    stop_loss = structural_level * 0.9993
                else:
                    stop_loss = structural_level * 1.0007
                stop_source = f"Structural swing point on {path['intermediate_label']}"

        if stop_loss is None:
            if direction == "BUY":
                stop_loss = matched_zone["bottom"] * 0.9993
            else:
                stop_loss = matched_zone["top"] * 1.0007
            stop_source = "Zone edge"

        if direction == "BUY" and entry_price <= stop_loss:
            log(f"{path['label']}: STOP - buy stop already invalidated")
            continue
        if direction == "SELL" and entry_price >= stop_loss:
            log(f"{path['label']}: STOP - sell stop already invalidated")
            continue

        risk = abs(entry_price - stop_loss)

        if path["swing_style"]:
            liquidity_target = _find_liquidity_target(path["source_df"], direction_word, entry_price)
            if liquidity_target is None:
                log(f"{path['label']}: skipped - no liquidity target found")
                continue
            reward = abs(liquidity_target - entry_price)
            rr = reward / risk if risk > 0 else 0
            if rr < MIN_SWING_RR:
                log(f"{path['label']}: skipped - R:R {round(rr, 2)} below minimum {MIN_SWING_RR}")
                continue
            take_profit = liquidity_target
            target_source = "Nearest opposing liquidity (swing point)"
        else:
            if direction == "BUY":
                take_profit = entry_price + risk * path["target_multiplier"]
            else:
                take_profit = entry_price - risk * path["target_multiplier"]
            target_source = f"Fixed {path['target_multiplier']}x risk multiplier"

        if daily_direction is None or direction == daily_direction:
            confidence = "high"
            confidence_label = "متوافقة مع الاتجاه العام (Daily)"
        else:
            confidence = "low"
            confidence_label = "عكس الاتجاه العام (Daily) - تصحيح مؤقت محتمل"

        reason = _build_reason(
            path["label"], direction_word, matched_zone,
            path["intermediate_label"], intermediate_type,
            path["entry_label"], entry_confirmation, confidence_label,
            stop_source, target_source,
        )

        signals.append({
            "symbol": Config.SYMBOL,
            "direction": direction,
            "entry": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "reason": reason,
            "trade_label": path["label"],
            "confidence": confidence,
            "confidence_label": confidence_label,
            "timestamp": datetime.now(),
        })

    if not signals:
        log("No trade opportunity found on any path (Swing-Daily, Swing-H4, Scalp-H1)")

    return signals


def analyze_market(debug: bool = False) -> list:
    daily_df = dc.get_candles("D1", count=120)
    h4_df = dc.get_candles("H4", count=120)
    h1_df = dc.get_candles("H1", count=150)
    m15_df = dc.get_candles("M15", count=150)
    m5_df = dc.get_candles("M5", count=100)

    return analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug=debug)
