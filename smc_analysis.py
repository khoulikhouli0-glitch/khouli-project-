import pandas as pd


def find_swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
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


def detect_structure(df: pd.DataFrame) -> dict:
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
        if last_close > prev_high:
            trend = "bullish"
            last_event = "CHoCH"
            level = prev_high
        elif last_close < prev_low:
            trend = "bearish"
            last_event = "CHoCH"
            level = prev_low

    return {"trend": trend, "last_event": last_event, "level": level}


def get_premium_discount_zone(df: pd.DataFrame, lookback: int = 50) -> dict:
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


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20, check_last: int = 4) -> dict:
    for i in range(1, check_last + 1):
        candle = df.iloc[-i]
        recent = df.iloc[max(0, len(df) - lookback - i): len(df) - i]

        if len(recent) == 0:
            continue

        prior_high = recent["high"].max()
        prior_low = recent["low"].min()

        swept_high = candle["high"] > prior_high and candle["close"] < prior_high
        swept_low = candle["low"] < prior_low and candle["close"] > prior_low

        if swept_high:
            return {"swept": True, "direction": "sell_side_taken", "level": prior_high}
        if swept_low:
            return {"swept": True, "direction": "buy_side_taken", "level": prior_low}

    return {"swept": False, "direction": None, "level": None}


def find_last_order_block(df: pd.DataFrame, direction: str, lookback: int = 30):
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


def find_recent_fvg(df: pd.DataFrame, direction: str, lookback: int = 30):
    recent = df.tail(lookback).reset_index(drop=True)

    for i in range(len(recent) - 3, 0, -1):
        c1 = recent.iloc[i]
        c3 = recent.iloc[i + 2]

        if direction == "bullish" and c3["low"] > c1["high"]:
            return {"top": c3["low"], "bottom": c1["high"], "time": c1["time"]}
        if direction == "bearish" and c3["high"] < c1["low"]:
            return {"top": c1["low"], "bottom": c3["high"], "time": c1["time"]}

    return None


def price_in_zone(price: float, zone: dict, tolerance_pct: float = 0.0005) -> bool:
    if zone is None:
        return False
    top = zone["top"] * (1 + tolerance_pct)
    bottom = zone["bottom"] * (1 - tolerance_pct)
    return bottom <= price <= top
