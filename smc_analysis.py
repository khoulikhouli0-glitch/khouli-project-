"""
محرك تحليل SMC (Smart Money Concepts)
يحتوي على المنطق الأساسي:
- تحديد السوينج بوينتس (Swing Highs/Lows)
- تحديد هيكل السوق (BOS / CHoCH) -> التحيز
- تحديد مناطق Premium/Discount
- كشف Liquidity Sweep
- كشف Order Blocks
- كشف Fair Value Gaps (FVG)
"""
import pandas as pd


# ---------- Swing Points ----------

def find_swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
    """
    يحدد نقاط السوينج (قمم وقيعان محلية) بمقارنة كل شمعة بجيرانها.
    يرجع df فيه أعمدة إضافية: is_swing_high, is_swing_low
    """
    df = df.copy()
    df["is_swing_high"] = False
    df["is_swing_low"] = False

    for i in range(left, len(df) - right):
        window_high = df["high"].iloc[i - left : i + right + 1]
        window_low = df["low"].iloc[i - left : i + right + 1]

        if df["high"].iloc[i] == window_high.max():
            df.loc[df.index[i], "is_swing_high"] = True
        if df["low"].iloc[i] == window_low.min():
            df.loc[df.index[i], "is_swing_low"] = True

    return df


# ---------- Market Structure: BOS / CHoCH ----------

def detect_structure(df: pd.DataFrame) -> dict:
    """
    يحدد اتجاه الهيكل الحالي بناءً على آخر سوينجات:
    - BOS (Break of Structure): كسر في اتجاه الترند الحالي -> تأكيد استمرار
    - CHoCH (Change of Character): كسر عكس الترند الحالي -> احتمال انعكاس
    يرجع dict فيه: trend ("bullish"/"bearish"/"unclear"), last_event, level
    """
    swings = find_swing_points(df)
    highs = swings[swings["is_swing_high"]][["time", "high"]].tail(4)
    lows = swings[swings["is_swing_low"]][["time", "low"]].tail(4)

    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "unclear", "last_event": None, "level": None}

    last_close = df["close"].iloc[-1]
    last_high = highs["high"].iloc[-1]
    prev_high = highs["high"].iloc[-2]
    last_low = lows["low"].iloc[-1]
    prev_low = lows["low"].iloc[-2]

    # ترند صاعد: قمم وقيعان أعلى من سابقتها
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
        # هيكل غير واضح - نفحص هل حصل CHoCH
        if last_close > prev_high:
            trend = "bullish"
            last_event = "CHoCH"
            level = prev_high
        elif last_close < prev_low:
            trend = "bearish"
            last_event = "CHoCH"
            level = prev_low

    return {"trend": trend, "last_event": last_event, "level": level}


# ---------- Premium / Discount Zones ----------

def get_premium_discount_zone(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    يحدد الرينج الحالي (أعلى قمة / أقل قاع في آخر lookback شمعة)
    ويحسب هل السعر الحالي في منطقة Premium (فوق 50%) أو Discount (تحت 50%)
    """
    recent = df.tail(lookback)
    range_high = recent["high"].max()
    range_low = recent["low"].min()
    midpoint = (range_high + range_low) / 2
    current_price = df["close"].iloc[-1]

    zone = "premium" if current_price > midpoint else "discount"

    return {
        "range_high": range_high,
        "range_low": range_low,
        "midpoint": midpoint,
        "zone": zone,
        "current_price": current_price,
    }


# ---------- Liquidity Sweep ----------

def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    يكشف هل آخر شمعة (أو الشمعتين الأخيرتين) عملت سويب لسيولة
    (كسرت قمة/قاع سابق بالفتيل ورجعت تقفل جواه) - دليل على جمع سيولة.
    """
    recent = df.iloc[-(lookback + 2) : -1]
    last_candle = df.iloc[-1]

    prior_high = recent["high"].max()
    prior_low = recent["low"].min()

    swept_high = last_candle["high"] > prior_high and last_candle["close"] < prior_high
    swept_low = last_candle["low"] < prior_low and last_candle["close"] > prior_low

    if swept_high:
        return {"swept": True, "direction": "sell_side_taken", "level": prior_high}
    if swept_low:
        return {"swept": True, "direction": "buy_side_taken", "level": prior_low}

    return {"swept": False, "direction": None, "level": None}


# ---------- Order Blocks ----------

def find_last_order_block(df: pd.DataFrame, direction: str, lookback: int = 30):
    """
    يحدد آخر Order Block صالح:
    - Bullish OB: آخر شمعة هابطة قبل حركة صاعدة قوية (impulsive move)
    - Bearish OB: آخر شمعة صاعدة قبل حركة هابطة قوية
    direction: "bullish" أو "bearish" (اتجاه الـ OB المطلوب البحث عنه)
    """
    recent = df.tail(lookback).reset_index(drop=True)
    avg_range = (recent["high"] - recent["low"]).mean()

    for i in range(len(recent) - 2, 0, -1):
        candle = recent.iloc[i]
        next_candle = recent.iloc[i + 1]
        move_size = abs(next_candle["close"] - candle["open"])
        is_impulsive = move_size > avg_range * 1.5

        if not is_impulsive:
            continue

        if direction == "bullish":
            is_down_candle = candle["close"] < candle["open"]
            moved_up = next_candle["close"] > candle["high"]
            if is_down_candle and moved_up:
                return {
                    "top": candle["open"],
                    "bottom": candle["low"],
                    "time": candle["time"],
                }
        elif direction == "bearish":
            is_up_candle = candle["close"] > candle["open"]
            moved_down = next_candle["close"] < candle["low"]
            if is_up_candle and moved_down:
                return {
                    "top": candle["high"],
                    "bottom": candle["open"],
                    "time": candle["time"],
                }

    return None


# ---------- Fair Value Gaps ----------

def find_recent_fvg(df: pd.DataFrame, direction: str, lookback: int = 30):
    """
    يكشف آخر Fair Value Gap (فجوة سعرية بين شمعة 1 و 3 لم تُملأ):
    - Bullish FVG: low الشمعة الثالثة > high الشمعة الأولى
    - Bearish FVG: high الشمعة الثالثة < low الشمعة الأولى
    """
    recent = df.tail(lookback).reset_index(drop=True)

    for i in range(len(recent) - 3, 0, -1):
        c1 = recent.iloc[i]
        c3 = recent.iloc[i + 2]

        if direction == "bullish" and c3["low"] > c1["high"]:
            return {"top": c3["low"], "bottom": c1["high"], "time": c1["time"]}
        if direction == "bearish" and c3["high"] < c1["low"]:
            return {"top": c1["low"], "bottom": c3["high"], "time": c1["time"]}

    return None


# ---------- Price inside zone check ----------

def price_in_zone(price: float, zone: dict, tolerance_pct: float = 0.0005) -> bool:
    """يفحص هل السعر الحالي داخل منطقة (OB أو FVG) مع هامش صغير."""
    if zone is None:
        return False
    top = zone["top"] * (1 + tolerance_pct)
    bottom = zone["bottom"] * (1 - tolerance_pct)
    return bottom <= price <= top
