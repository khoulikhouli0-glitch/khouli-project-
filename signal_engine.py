from datetime import datetime
import deriv_connector as dc
import smc_analysis as smc
from config import Config


def analyze_market(debug: bool = False) -> dict | None:
    def log(msg):
        if debug:
            print(f"[DEBUG] {msg}")

    htf_df = dc.get_candles(Config.HTF_BIAS, count=200)
    htf_structure = smc.detect_structure(htf_df)
    bias = htf_structure["trend"]
    log(f"HTF ({Config.HTF_BIAS}) bias={bias} last_event={htf_structure['last_event']}")

    if bias not in ("bullish", "bearish"):
        log("STOP: no clear HTF bias")
        return None

    mtf_df = dc.get_candles(Config.MTF_ZONE, count=100)
    pd_zone = smc.get_premium_discount_zone(mtf_df)
    log(f"MTF ({Config.MTF_ZONE}) zone={pd_zone['zone']} price={pd_zone['current_price']} mid={pd_zone['midpoint']}")

    if bias == "bullish" and pd_zone["zone"] != "discount":
        log("STOP: bullish bias but price not in discount zone")
        return None
    if bias == "bearish" and pd_zone["zone"] != "premium":
        log("STOP: bearish bias but price not in premium zone")
        return None

    ltf_df = dc.get_candles(Config.LTF_CONFIRM, count=100)
    sweep = smc.detect_liquidity_sweep(ltf_df)
    log(f"LTF ({Config.LTF_CONFIRM}) sweep={sweep}")

    if not sweep["swept"]:
        log("STOP: no liquidity sweep detected")
        return None

    if bias == "bullish" and sweep["direction"] != "buy_side_taken":
        log("STOP: sweep direction does not match bullish bias")
        return None
    if bias == "bearish" and sweep["direction"] != "sell_side_taken":
        log("STOP: sweep direction does not match bearish bias")
        return None

    ltf_structure = smc.detect_structure(ltf_df)
    log(f"LTF structure: trend={ltf_structure['trend']} last_event={ltf_structure['last_event']}")

    if ltf_structure["last_event"] not in ("BOS", "CHoCH") or ltf_structure["trend"] != bias:
        log("STOP: no confirming structure break on LTF matching bias")
        return None

    entry_df = dc.get_candles(Config.ENTRY_TF, count=100)
    ob = smc.find_last_order_block(entry_df, direction=bias)
    fvg = smc.find_recent_fvg(entry_df, direction=bias)
    log(f"Entry TF ({Config.ENTRY_TF}) OB={ob} FVG={fvg}")

    current_price = dc.get_current_price()
    ref_price = current_price["ask"] if bias == "bullish" else current_price["bid"]

    entry_zone = ob or fvg
    zone_type = "Order Block" if ob else ("Fair Value Gap" if fvg else "Fallback distance")

    direction = "BUY" if bias == "bullish" else "SELL"
    entry_price = ref_price

    if entry_zone is not None:
        if direction == "BUY":
            stop_loss = entry_zone["bottom"] * 0.9995
        else:
            stop_loss = entry_zone["top"] * 1.0005
    else:
        avg_range = (entry_df["high"] - entry_df["low"]).tail(20).mean()
        if direction == "BUY":
            stop_loss = entry_price - avg_range * 2
        else:
            stop_loss = entry_price + avg_range * 2

    if direction == "BUY":
        risk = entry_price - stop_loss
        take_profit = entry_price + risk * 1.5
    else:
        risk = stop_loss - entry_price
        take_profit = entry_price - risk * 1.5

    log(f"SIGNAL FOUND: {direction} entry={entry_price} sl={stop_loss} tp={take_profit}")

    reason = build_reason(bias, pd_zone, sweep, ltf_structure, zone_type)

    return {
        "symbol": Config.SYMBOL,
        "direction": direction,
        "entry": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "reason": reason,
        "timestamp": datetime.now(),
    }


def build_reason(bias, pd_zone, sweep, ltf_structure, zone_type) -> str:
    zone_label = "Premium" if pd_zone["zone"] == "premium" else "Discount"
    sweep_label = "buy-side liquidity" if sweep["direction"] == "buy_side_taken" else "sell-side liquidity"

    return (
        f"HTF bias ({Config.HTF_BIAS}): {bias}\n"
        f"Price inside {zone_label} zone on {Config.MTF_ZONE}\n"
        f"Swept {sweep_label} then {ltf_structure['last_event']} on {Config.LTF_CONFIRM}\n"
        f"Immediate entry after structure confirmation, stop based on {zone_type} on {Config.ENTRY_TF}"
    )
