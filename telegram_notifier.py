"""
Sends trade signals to a Telegram bot.
"""
import asyncio
from telegram import Bot
from config import Config

_bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)


def format_signal_message(signal: dict) -> str:
    emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    label = signal.get("trade_label", "")
    return (
        f"{emoji} <b>{signal['direction']} - {signal['symbol']}</b>\n"
        f"<b>{label}</b>\n\n"
        f"Entry: <code>{signal['entry']}</code>\n"
        f"Stop Loss: <code>{signal['stop_loss']}</code>\n"
        f"Take Profit: <code>{signal['take_profit']}</code>\n\n"
        f"<b>Reason:</b>\n{signal['reason']}\n\n"
        f"{signal['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}"
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
    print("[Telegram] Signal sent.")


def send_text(text: str):
    asyncio.run(_send_async(text))
