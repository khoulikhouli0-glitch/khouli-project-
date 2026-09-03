Wave and Fibonacci analysis.
Identifies the most relevant recent swing (wave) on a given timeframe -
a real swing high and swing low pair that represents the dominant
recent price move - regardless of how many candles it took to form.
Then computes a Fibonacci retracement zone on that wave, which acts
as a "zone of interest" for higher-timeframe context.
"""
import pandas as pd


def find_major_swing(df: pd.DataFrame, pivot_left: int = 3, pivot_right: int = 3):
    n = len(df)
    if n < (pivot_left + pivot_right + 5):
        return None

    highs = []
    lows = []
    for i in range(pivot_left, n - pivot_right):
        window_high = df["high"].iloc[i - pivot_left: i + pivot_right + 1]
        window_low = df["low"].iloc[i - pivot_left: i + pivot_right + 1]
        if df["high"].iloc[i] == window_high.max():
            highs.append((i, df["high"].iloc[i], df["time"].iloc[i]))
        if df["low"].iloc[i] == window_low.min():
            lows.append((i, df["low"].iloc[i], df["time"].iloc[i]))

    if not highs or not lows:
        return None

    last_high = highs[-1]
    last_low = lows[-1]

    if last_high[0] < last_low[0]:
        start_idx, start_price, start_time, start_type = (*last_high, "high")
        end_idx, end_price, end_time, end_type = (*last_low, "low")
        direction = "down"
    else:
        start_idx, start_price, start_time, start_type = (*last_low, "low")
        end_idx, end_price, end_time, end_type = (*last_high, "high")
        direction = "up"

    return {
        "start_price": start_price,
        "start_time": start_time,
        "start_type": start_type,
        "end_price": end_price,
        "end_time": end_time,
        "end_type": end_type,
        "direction": direction,
    }


def fibonacci_zone(wave: dict, low_ratio: float = 0.618, high_ratio: float = 0.79) -> dict:
    start = wave["start_price"]
    end = wave["end_price"]
    direction = wave["direction"]
    span = abs(end - start)

    if direction == "up":
        zone_bottom = end - span * high_ratio
        zone_top = end - span * low_ratio
    else:
        zone_bottom = end + span * low_ratio
        zone_top = end + span * high_ratio

    return {
        "top": max(zone_top, zone_bottom),
        "bottom": min(zone_top, zone_bottom),
        "direction": direction,
    }


def price_in_fib_zone(price: float, zone: dict, tolerance_pct: float = 0.0008) -> bool:
    if zone is None:
        return False
    top = zone["top"] * (1 + tolerance_pct)
    bottom = zone["bottom"] * (1 - tolerance_pct)
    return bottom <= price <= top
