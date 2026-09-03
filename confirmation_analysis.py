Entry confirmation patterns on small timeframes (M15/M5).
These are the final trigger patterns checked once price has reached
a zone of interest identified on the higher timeframes. Any single
one of them is enough to confirm an entry.
"""
import pandas as pd


def detect_engulfing(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 2:
        return False

    prev = df.iloc[-2]
    last = df.iloc[-1]

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


def detect_break_and_retest(df: pd.DataFrame, direction: str, lookback: int = 20) -> bool:
    if len(df) < lookback + 3:
        return False

    window = df.iloc[-(lookback + 3):-3]
    last_two = df.iloc[-3:]

    if direction == "bullish":
        level = window["high"].max()
        broke = (last_two["close"] > level).any()
        if not broke:
            return False
        last = df.iloc[-1]
        retested = last["low"] <= level * 1.001 and last["close"] > level
        return retested

    if direction == "bearish":
        level = window["low"].min()
        broke = (last_two["close"] < level).any()
        if not broke:
            return False
        last = df.iloc[-1]
        retested = last["high"] >= level * 0.999 and last["close"] < level
        return retested

    return False


def detect_structure_break(df: pd.DataFrame, direction: str, pivot_left: int = 3, pivot_right: int = 3) -> bool:
    n = len(df)
    if n < (pivot_left + pivot_right + 3):
        return False

    if direction == "bullish":
        pivots = []
        for i in range(pivot_left, n - pivot_right - 1):
            window = df["high"].iloc[i - pivot_left: i + pivot_right + 1]
            if df["high"].iloc[i] == window.max():
                pivots.append(df["high"].iloc[i])
        if not pivots:
            return False
        last_close = df["close"].iloc[-1]
        return last_close > pivots[-1]

    if direction == "bearish":
        pivots = []
        for i in range(pivot_left, n - pivot_right - 1):
            window = df["low"].iloc[i - pivot_left: i + pivot_right + 1]
            if df["low"].iloc[i] == window.min():
                pivots.append(df["low"].iloc[i])
        if not pivots:
            return False
        last_close = df["close"].iloc[-1]
        return last_close < pivots[-1]

    return False


def detect_rejection_candle(df: pd.DataFrame, direction: str, wick_ratio: float = 2.0) -> bool:
    if len(df) < 1:
        return False

    last = df.iloc[-1]
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


def get_confirmation(df: pd.DataFrame, direction: str) -> str | None:
    if detect_engulfing(df, direction):
        return "Engulfing Candle"
    if detect_break_and_retest(df, direction):
        return "Break and Retest"
    if detect_structure_break(df, direction):
        return "Structure Break (CHoCH)"
    if detect_rejection_candle(df, direction):
        return "Rejection Candle"
    return None
