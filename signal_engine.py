"""
محرك القرار: يجمع كل مراحل التحليل (Top-Down) ويطلع صفقة مقترحة مع السبب،
أو None لو الشروط لسه ما تحققتش.

تسلسل التحليل:
1) H4 -> تحديد التحيز العام (BOS/CHoCH)
2) H1 -> تحديد منطقة Premium/Discount
3) M5 -> كشف Liquidity Sweep + CHoCH تأكيدي
4) M5/M1 -> تحديد آخر Order Block / FVG والدخول عند الرجوع له
"""
from datetime import datetime
import deriv_connector as dc
import smc_analysis as smc
from config import Config


def analyze_market() -> dict | None:
    # 1) التحيز من الفريم العالي
    htf_df = dc.get_candles(Config.HTF_BIAS, count=200)
    htf_structure = smc.detect_structure(htf_df)
    bias = htf_structure["trend"]

    if bias not in ("bullish", "bearish"):
        return None  # لا يوجد تحيز واضح، لا تداول

    # 2) منطقة Premium/Discount من الفريم المتوسط
    mtf_df = dc.get_candles(Config.MTF_ZONE, count=100)
    pd_zone = smc.get_premium_discount_zone(mtf_df)

    # قاعدة: شراء فقط من Discount، بيع فقط من Premium
    if bias == "bullish" and pd_zone["zone"] != "discount":
        return None
    if bias == "bearish" and pd_zone["zone"] != "premium":
        return None

    # 3) تأكيد على الفريم الصغير: Liquidity Sweep + CHoCH
    ltf_df = dc.get_candles(Config.LTF_CONFIRM, count=100)
    sweep = smc.detect_liquidity_sweep(ltf_df)

    if not sweep["swept"]:
        return None

    # السويب لازم يكون في الاتجاه الصح (سويب سيولة بيع قبل الشراء، والعكس)
    if bias == "bullish" and sweep["direction"] != "buy_side_taken":
        return None
    if bias == "bearish" and sweep["direction"] != "sell_side_taken":
        return None

    ltf_structure = smc.detect_structure(ltf_df)
    if ltf_structure["last_event"] not in ("BOS", "CHoCH") or ltf_structure["trend"] != bias:
        return None

    # 4) تحديد منطقة الدخول (OB أو FVG) على فريم الدخول
    entry_df = dc.get_candles(Config.ENTRY_TF, count=100)
    ob = smc.find_last_order_block(entry_df, direction=bias)
    fvg = smc.find_recent_fvg(entry_df, direction=bias)

    current_price = dc.get_current_price()
    ref_price = current_price["ask"] if bias == "bullish" else current_price["bid"]

    entry_zone = None
    zone_type = None
    if ob and smc.price_in_zone(ref_price, ob):
        entry_zone = ob
        zone_type = "Order Block"
    elif fvg and smc.price_in_zone(ref_price, fvg):
        entry_zone = fvg
        zone_type = "Fair Value Gap"

    if entry_zone is None:
        return None  # السعر لسه ما وصلش لمنطقة دخول صالحة

    # ---- بناء الصفقة ----
    direction = "BUY" if bias == "bullish" else "SELL"
    entry_price = ref_price

    if direction == "BUY":
        stop_loss = entry_zone["bottom"] * 0.9995  # هامش صغير تحت المنطقة
        risk = entry_price - stop_loss
        take_profit = entry_price + risk * 1.5
    else:
        stop_loss = entry_zone["top"] * 1.0005
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
    bias_ar = "صاعد" if bias == "bullish" else "هابط"
    zone_ar = "Premium" if pd_zone["zone"] == "premium" else "Discount"
    sweep_ar = "سيولة شراء (Buy-side)" if sweep["direction"] == "buy_side_taken" else "سيولة بيع (Sell-side)"

    return (
        f"• تحيز الفريم العالي ({Config.HTF_BIAS}): {bias_ar}\n"
        f"• السعر داخل منطقة {zone_ar} على فريم {Config.MTF_ZONE}\n"
        f"• تم أخذ {sweep_ar} ثم {ltf_structure['last_event']} على فريم {Config.LTF_CONFIRM}\n"
        f"• الدخول عند رجوع السعر لمنطقة {zone_type} على فريم {Config.ENTRY_TF}"
    )
