import pandas as pd


def detect_engulfing(df: pd.DataFrame, direction: str, at: int = -1) -> bool:
    if len(df) < 2:
        return False

    prev = df.iloc[at - 1]
    last = df.iloc[at]

    if direction == "bullish":
        prev_is_down = prev["close"] < prev["open"]
        last_is_up = last["close"] > last["open"]
        engulfs = last["open"] <= prev["close"] and last["close"] >= prev["open"]
        return prev_is_down and last_is_up and engulfs

    if direction == "bearish":
        prev_is_up = prev["close"] > prev["open"]
        last_is_down = last["close"] < last["open"]
        engulfs = last["open"] >= prev["close"] and last["close"] <= prev["open"]
        return prev_is_up and last_is_down and engulfs

    return False


def detect_break_and_retest(df: pd.DataFrame, direction: str, lookback: int = 20, at: int = -1) -> bool:
    end_pos = len(df) + at + 1 if at < 0 else at + 1
    if end_pos < lookback + 3:
        return False

    window = df.iloc[end_pos - lookback - 3: end_pos - 3]
    last_two = df.iloc[end_pos - 3: end_pos]
    last = df.iloc[end_pos - 1]

    if direction == "bullish":
        level = window["high"].max()
        broke = (last_two["close"] > level).any()
        if not broke:
            return False
        retested = last["low"] <= level * 1.001 and last["close"] > level
        return retested

    if direction == "bearish":
        level = window["low"].min()
        broke = (last_two["close"] < level).any()
        if not broke:
            return False
        retested = last["high"] >= level * 0.999 and last["close"] < level
        return retested

    return False


def detect_structure_break(df: pd.DataFrame, direction: str, pivot_left: int = 3, pivot_right: int = 3, at: int = -1) -> bool:
    end_pos = len(df) + at + 1 if at < 0 else at + 1
    n = end_pos
    if n < (pivot_left + pivot_right + 3):
        return False

    sub = df.iloc[:n]

    if direction == "bullish":
        pivots = []
        for i in range(pivot_left, n - pivot_right - 1):
            window = sub["high"].iloc[i - pivot_left: i + pivot_right + 1]
            if sub["high"].iloc[i] == window.max():
                pivots.append(sub["high"].iloc[i])
        if not pivots:
            return False
        last_close = sub["close"].iloc[-1]
        return last_close > pivots[-1]

    if direction == "bearish":
        pivots = []
        for i in range(pivot_left, n - pivot_right - 1):
            window = sub["low"].iloc[i - pivot_left: i + pivot_right + 1]
            if sub["low"].iloc[i] == window.min():
                pivots.append(sub["low"].iloc[i])
        if not pivots:
            return False
        last_close = sub["close"].iloc[-1]
        return last_close < pivots[-1]

    return False


def detect_rejection_candle(df: pd.DataFrame, direction: str, wick_ratio: float = 2.0, at: int = -1) -> bool:
    if len(df) < 1:
        return False

    last = df.iloc[at]
    body = abs(last["close"] - last["open"])
    total_range = last["high"] - last["low"]
    if total_range <= 0 or body == 0:
        return False

    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]

    if direction == "bullish":
        return lower_wick >= body * wick_ratio and lower_wick > upper_wick

    if direction == "bearish":
        return upper_wick >= body * wick_ratio and upper_wick > lower_wick

    return False


def get_confirmation(df: pd.DataFrame, direction: str, lookback: int = 10) -> str | None:
    n = len(df)
    start = max(2, n - lookback)
    for i in range(n - 1, start - 1, -1):
        pos = i - n
        if detect_engulfing(df, direction, at=pos):
            return "Engulfing Candle"
        if detect_break_and_retest(df, direction, at=pos):
            return "Break and Retest"
        if detect_structure_break(df, direction, at=pos):
            return "Structure Break (CHoCH)"
        if detect_rejection_candle(df, direction, at=pos):
            return "Rejection Candle"
    return None
