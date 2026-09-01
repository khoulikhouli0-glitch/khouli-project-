"""
طبقة الاتصال بـ Deriv API (WebSocket) - بديل MT5.
شغالة على أي نظام تشغيل (Linux/Mac/Windows) بدون أي قيود.
مرجع: https://developers.deriv.com/docs/
"""
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

# خرائط رموز الذهب وأشهر الأدوات المشابهة عند Deriv
SYMBOL_MAP = {
    "XAUUSD": "frxXAUUSD",
}


async def _request(payload: dict) -> dict:
    """يفتح اتصال WebSocket مؤقت، يبعت الطلب، ويرجع الرد."""
    url = DERIV_WS_URL.format(app_id=Config.DERIV_APP_ID)
    async with websockets.connect(url) as ws:
        if Config.DERIV_API_TOKEN:
            await ws.send(json.dumps({"authorize": Config.DERIV_API_TOKEN}))
            await ws.recv()  # رد التوثيق - بيانات الشموع العامة مش محتاجة توثيق أصلاً
        await ws.send(json.dumps(payload))
        response = await ws.recv()
        return json.loads(response)


def connect() -> bool:
    """يتأكد إن الاتصال بـ Deriv شغال (مفيش اتصال دائم محتاج نفتحه هنا)."""
    try:
        result = asyncio.run(_request({"ping": 1}))
        if result.get("ping") == "pong":
            print("[Deriv] تم الاتصال بنجاح.")
            return True
        print(f"[Deriv] رد غير متوقع: {result}")
        return False
    except Exception as e:
        print(f"[Deriv] فشل الاتصال: {e}")
        return False


def disconnect():
    """لا يوجد اتصال دائم لإغلاقه - كل طلب بيفتح ويقفل اتصاله بنفسه."""
    pass


def get_candles(timeframe_str: str, count: int = 300) -> pd.DataFrame:
    """
    يسحب آخر `count` شمعة للفريم المطلوب ويرجعها كـ DataFrame
    بأعمدة: time, open, high, low, close, volume
    """
    granularity = TIMEFRAME_SECONDS.get(timeframe_str)
    if granularity is None:
        raise ValueError(f"فريم غير مدعوم: {timeframe_str}")

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
        raise RuntimeError(f"خطأ من Deriv API: {result['error'].get('message')}")

    candles = result.get("candles", [])
    if not candles:
        raise RuntimeError(f"لم يتم سحب بيانات {symbol} على فريم {timeframe_str}")

    df = pd.DataFrame(candles)
    df["time"] = pd.to_datetime(df["epoch"], unit="s")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = 0  # Deriv forex/commodities ما بتوفرش حجم تداول تقليدي

    return df[["time", "open", "high", "low", "close", "volume"]]


def get_current_price() -> dict:
    """يرجع آخر سعر (tick) للرمز."""
    symbol = SYMBOL_MAP.get(Config.SYMBOL, Config.SYMBOL)
    result = asyncio.run(_request({"ticks": symbol, "subscribe": 0}))

    if "error" in result:
        raise RuntimeError(f"خطأ من Deriv API: {result['error'].get('message')}")

    tick = result.get("tick", {})
    price = tick.get("quote")
    if price is None:
        raise RuntimeError("لم يتم جلب السعر الحالي من Deriv")

    return {"bid": price, "ask": price, "time": tick.get("epoch")}
