"""
إرسال إشارات الصفقات إلى بوت تلغرام.
"""
import asyncio
from telegram import Bot
from config import Config

_bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)


def format_signal_message(signal: dict) -> str:
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    return (
        f"{emoji} <b>إشارة {signal['direction']} - {signal['symbol']}</b>\n\n"
        f"💰 الدخول: <code>{signal['entry']}</code>\n"
        f"🛑 الستوب: <code>{signal['stop_loss']}</code>\n"
        f"🎯 الهدف: <code>{signal['take_profit']}</code>\n\n"
        f"📊 <b>السبب:</b>\n{signal['reason']}\n\n"
        f"⏱ {signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def _send_async(text: str):
    await _bot.send_message(
        chat_id=Config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="HTML",
    )


def send_signal(signal: dict):
    text = format_signal_message(signal)
    asyncio.run(_send_async(text))
    print("[Telegram] تم إرسال الإشارة.")


def send_text(text: str):
    asyncio.run(_send_async(text))
