"""
Signal engine: runs the full top-down SMC analysis and returns a trade
suggestion with a reason, or None if conditions are not met yet.

Sequence:
1) H4  -> overall bias (BOS/CHoCH)
2) H1  -> Premium/Discount zone
3) M5  -> Liquidity Sweep + confirming CHoCH
4) M1  -> enter immediately after structure confirmation, using the
   nearest Order Block/FVG for the stop if available, otherwise a
   fallback stop distance based on recent average range.
"""
from datetime import datetime
import deriv_connector as dc
import smc_analysis as smc
from config import Config


def analyze_market() -> dict | None:
    htf_df = dc.get_candles(Config.HTF_BIAS, count=200)
    htf_structure = smc.detect_structure(htf_df)
    bias = htf_structure["trend"]

    if bias not in ("bullish", "bearish"):
        return None

    mtf_df = dc.get_candles(Config.MTF_ZONE, count=100)
    pd_zone = smc.get_premium_discount_zone(mtf_df)

    if bias == "bullish" and pd_zone["zone"] != "discount":
        return None
    if bias == "bearish" and pd_zone["zone"] != "premium":
        return None

    ltf_df = dc.get_candles(Config.LTF_CONFIRM, count=100)
    sweep = smc.detect_liquidity_sweep(ltf_df)

    if not sweep["swept"]:
        return None

    if bias == "bullish" and sweep["direction"] != "buy_side_taken":
        return None
    if bias == "bearish" and sweep["direction"] != "sell_side_taken":
        return None

    ltf_structure = smc.detect_structure(ltf_df)
    if ltf_structure["last_event"] not in ("BOS", "CHoCH") or ltf_structure["trend"] != bias:
        return None

    entry_df = dc.get_candles(Config.ENTRY_TF, count=100)
    ob = smc.find_last_order_block(entry_df, direction=bias)
    fvg = smc.find_recent_fvg(entry_df, direction=bias)

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
