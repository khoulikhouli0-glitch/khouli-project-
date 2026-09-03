import asyncio
import json

import pandas as pd
import websockets

from config import Config

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

SYMBOL_MAP = {
    "XAUUSD": "frxXAUUSD",
}


async def _request(payload: dict) -> dict:
    url = DERIV_WS_URL.format(app_id=Config.DERIV_APP_ID)
    async with websockets.connect(url) as ws:
        if Config.DERIV_API_TOKEN:
            await ws.send(json.dumps({"authorize": Config.DERIV_API_TOKEN}))
            await ws.recv()
        await ws.send(json.dumps(payload))
        response = await ws.recv()
        return json.loads(response)


def connect() -> bool:
    try:
        result = asyncio.run(_request({"ping": 1}))
        if result.get("ping") == "pong":
            print("[Deriv] Connected successfully.")
            return True
        print(f"[Deriv] Unexpected response: {result}")
        return False
    except Exception as e:
        print(f"[Deriv] Connection failed: {e}")
        return False


def disconnect():
    pass


def get_candles(timeframe_str: str, count: int = 300) -> pd.DataFrame:
    granularity = TIMEFRAME_SECONDS.get(timeframe_str)
    if granularity is None:
        raise ValueError(f"Unsupported timeframe: {timeframe_str}")

    symbol = SYMBOL_MAP.get(Config.SYMBOL, Config.SYMBOL)
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "start": 1,
        "style": "candles",
        "granularity": granularity,
    }
    result = asyncio.run(_request(payload))

    if "error" in result:
        raise RuntimeError(f"Deriv API error: {result['error'].get('message')}")

    candles = result.get("candles", [])
    if not candles:
        raise RuntimeError(f"No candles returned for {symbol} on {timeframe_str}")

    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["epoch"], unit="s")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = 0

    return df[["time", "open", "high", "low", "close", "volume"]]


def get_historical_candles(timeframe_str: str, months: int = 3) -> pd.DataFrame:
    granularity = TIMEFRAME_SECONDS.get(timeframe_str)
    if granularity is None:
        raise ValueError(f"Unsupported timeframe: {timeframe_str}")

    symbol = SYMBOL_MAP.get(Config.SYMBOL, Config.SYMBOL)
    seconds_needed = months * 30 * 24 * 3600
    remaining_seconds = seconds_needed
    current_end = "latest"
    all_dfs = []

    while remaining_seconds > 0:
        payload = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 5000,
            "end": current_end,
            "start": 1,
            "style": "candles",
            "granularity": granularity,
        }
        result = asyncio.run(_request(payload))

        if "error" in result:
            raise RuntimeError(f"Deriv API error: {result['error'].get('message')}")

        candles = result.get("candles", [])
        if not candles:
            break

        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = 0
        all_dfs.append(df[["time", "open", "high", "low", "close", "volume", "epoch"]])

        earliest_epoch = int(df["epoch"].iloc[0])
        current_end = earliest_epoch - 1
        remaining_seconds -= granularity * len(df)

        if len(df) < 5000:
            break

    if not all_dfs:
        raise RuntimeError(f"No historical candles for {symbol} on {timeframe_str}")

    full_df = pd.concat(all_dfs, ignore_index=True)
    full_df = full_df.drop_duplicates(subset="epoch").sort_values("time").reset_index(drop=True)
    return full_df[["time", "open", "high", "low", "close", "volume"]]


def get_current_price() -> dict:
    symbol = SYMBOL_MAP.get(Config.SYMBOL, Config.SYMBOL)
    result = asyncio.run(_request({"ticks": symbol}))

    if "error" in result:
        raise RuntimeError(f"Deriv API error: {result['error'].get('message')}")

    tick = result.get("tick", {})
    price = tick.get("quote")
    if price is None:
        raise RuntimeError("Could not fetch current price from Deriv")

    return {"bid": price, "ask": price, "time": tick.get("epoch")}
