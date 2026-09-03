from datetime import datetime

import deriv_connector as dc
import smc_analysis as smc
import zone_analysis as zones
import confirmation_analysis as confirm
from config import Config

CONFIRM_TF = "M15"
TRIGGER_TF = "M5"


def _get_bias(df) -> str:
    structure = smc.detect_structure(df)
    return structure["trend"]


def analyze_market(debug: bool = False) -> dict | None:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    daily_df = dc.get_candles("D1", count=120)
    h4_df = dc.get_candles("H4", count=120)
    h1_df = dc.get_candles("H1", count=150)

    daily_bias = _get_bias(daily_df)
    h4_bias = _get_bias(h4_df)
    log(f"Daily bias={daily_bias}  H4 bias={h4_bias}")

    bias = h4_bias if h4_bias in ("bullish", "bearish") else daily_bias
    if bias not in ("bullish", "bearish"):
        log("STOP: no usable bias on Daily or H4")
        return None

    daily_zones = zones.collect_zones(daily_df, direction=bias)
    h4_zones = zones.collect_zones(h4_df, direction=bias)
    h1_zones = zones.collect_zones(h1_df, direction=bias)
    log(f"Daily zones={daily_zones}")
    log(f"H4 zones={h4_zones}")
    log(f"H1 zones={h1_zones}")

    confluence_zones = zones.find_confluence(daily_zones, h4_zones, "Daily", "H4")
    confluence_zones += zones.find_confluence(daily_zones, h1_zones, "Daily", "H1")
    confluence_zones += zones.find_confluence(h4_zones, h1_zones, "H4", "H1")
    all_zones = confluence_zones + daily_zones + h4_zones + h1_zones

    if not all_zones:
        log("STOP: no zones of interest found on Daily or H4")
        return None

    m1_df = dc.get_candles("M1", count=2)
    ref_price = float(m1_df["close"].iloc[-1])

    matched_zone = zones.price_in_any_zone(ref_price, confluence_zones)
    source_tf = "Confluence"
    if matched_zone is None:
        matched_zone = zones.price_in_any_zone(ref_price, daily_zones)
        source_tf = "Daily"
    if matched_zone is None:
        matched_zone = zones.price_in_any_zone(ref_price, h4_zones)
        source_tf = "H4"
    if matched_zone is None:
        matched_zone = zones.price_in_any_zone(ref_price, h1_zones)
        source_tf = "H1"

    if matched_zone is None:
        log("STOP: price is not currently inside any zone of interest")
        return None

    log(f"Price is inside zone: {matched_zone} (source_tf={source_tf})")

    ltf_df = dc.get_candles(TRIGGER_TF, count=100)
    sweep = smc.detect_liquidity_sweep(ltf_df)
    ltf_structure = smc.detect_structure(ltf_df)

    trigger_present = (
        sweep["swept"]
        or ltf_structure["last_event"] in ("BOS", "CHoCH")
    )
    if not trigger_present:
        log("STOP: no trigger (sweep/structure change) yet at the zone")
        return None

    confirm_df = dc.get_candles(CONFIRM_TF, count=60)
    confirmation = confirm.get_confirmation(confirm_df, direction=bias)
    log(f"Confirmation on {CONFIRM_TF}: {confirmation}")

    if confirmation is None:
        log("STOP: no confirmation pattern yet on entry timeframe")
        return None

    direction = "BUY" if bias == "bullish" else "SELL"
    entry_price = ref_price

    if direction == "BUY":
        stop_loss = matched_zone["bottom"] * 0.9993
    else:
        stop_loss = matched_zone["top"] * 1.0007

    if "Daily" in source_tf:
        target_multiplier = 3.0
        trade_label = f"Swing ({source_tf})"
    elif "H4" in source_tf:
        target_multiplier = 2.0
        trade_label = f"Swing ({source_tf})"
    elif "H1" in source_tf:
        target_multiplier = 1.5
        trade_label = f"Scalp ({source_tf})"
    else:
        target_multiplier = 1.2
        trade_label = f"Scalp ({source_tf})"

    if direction == "BUY":
        risk = entry_price - stop_loss
        take_profit = entry_price + risk * target_multiplier
    else:
        risk = stop_loss - entry_price
        take_profit = entry_price - risk * target_multiplier

    reason = build_reason(bias, source_tf, matched_zone, sweep, ltf_structure, confirmation, trade_label)

    return {
        "symbol": Config.SYMBOL,
        "direction": direction,
        "entry": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "reason": reason,
        "trade_label": trade_label,
        "timestamp": datetime.now(),
    }


def build_reason(bias, source_tf, matched_zone, sweep, ltf_structure, confirmation, trade_label) -> str:
    trigger_desc = []
    if sweep["swept"]:
        trigger_desc.append("liquidity sweep")
    if ltf_structure["last_event"] in ("BOS", "CHoCH"):
        trigger_desc.append(ltf_structure["last_event"])
    trigger_text = " + ".join(trigger_desc) if trigger_desc else "context alignment"

    return (
        f"Type: {trade_label}\n"
        f"Bias: {bias}\n"
        f"Zone of interest ({source_tf}): {matched_zone['source']}\n"
        f"Trigger on {TRIGGER_TF}: {trigger_text}\n"
        f"Confirmation on {CONFIRM_TF}: {confirmation}"
    )
