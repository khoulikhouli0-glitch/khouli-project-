from datetime import datetime

import deriv_connector as dc
import smc_analysis as smc
import zone_analysis as zones
import confirmation_analysis as confirm
from config import Config

TOUCH_LOOKBACK = 40
INTERMEDIATE_WINDOW_CANDLES = 6


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


def _build_reason(path_label, direction_word, matched_zone, intermediate_label,
                   intermediate_type, entry_label, entry_confirmation, confidence_label):
    return (
        f"Type: {path_label}\n"
        f"Bias: {direction_word}\n"
        f"Zone of interest: {matched_zone['source']}\n"
        f"Intermediate confirmation on {intermediate_label}: {intermediate_type}\n"
        f"Entry confirmation on {entry_label}: {entry_confirmation}\n"
        f"Confidence: {confidence_label}"
    )


def analyze_market_from_data(daily_df, h4_df, h1_df, m15_df, m5_df, debug: bool = False) -> list:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    daily_bias = _get_bias(daily_df)
    h4_bias = _get_bias(h4_df)
    h1_bias = _get_bias(h1_df)
    log(f"Daily bias={daily_bias}  H4 bias={h4_bias}  H1 bias={h1_bias}")

    daily_direction = None
    if daily_bias == "bullish":
        daily_direction = "BUY"
    elif daily_bias == "bearish":
        daily_direction = "SELL"

    paths = []
    if daily_bias in ("bullish", "bearish"):
        paths.append({
            "label": "Swing (Daily)",
            "bias": daily_bias,
            "source_df": daily_df,
            "intermediate_df": h4_df,
            "intermediate_label": "H4",
            "entry_df": m15_df,
            "entry_label": "M15",
            "target_multiplier": 3.0,
        })
    if h4_bias in ("bullish", "bearish"):
        paths.append({
            "label": "Swing (H4)",
            "bias": h4_bias,
            "source_df": h4_df,
            "intermediate_df": h1_df,
            "intermediate_label": "H1",
            "entry_df": m15_df,
            "entry_label": "M15",
            "target_multiplier": 2.0,
        })
    if h1_bias in ("bullish", "bearish") and m5_df is not None:
        paths.append({
            "label": "Scalp (H1)",
            "bias": h1_bias,
            "source_df": h1_df,
            "intermediate_df": m15_df,
            "intermediate_label": "M15",
            "entry_df": m5_df,
            "entry_label": "M5",
            "target_multiplier": 1.5,
        })

    signals = []

    for path in paths:
        direction_word = path["bias"]
        zones_list = zones.collect_zones(path["source_df"], direction=direction_word)
        if not zones_list:
            log(f"{path['label']}: no zones of interest")
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

        if direction == "BUY":
            stop_loss = matched_zone["bottom"] * 0.9993
            if entry_price <= stop_loss:
                log(f"{path['label']}: STOP - buy zone already invalidated")
                continue
            risk = entry_price - stop_loss
            take_profit = entry_price + risk * path["target_multiplier"]
        else:
            stop_loss = matched_zone["top"] * 1.0007
            if entry_price >= stop_loss:
                log(f"{path['label']}: STOP - sell zone already invalidated")
                continue
            risk = stop_loss - entry_price
            take_profit = entry_price - risk * path["target_multiplier"]

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
