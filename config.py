"""
تحميل الإعدادات من ملف .env
انسخ .env.example إلى .env واملأ بياناتك قبل التشغيل.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Deriv API
    # 1089 هو الـ App ID العام الرسمي من Deriv، متاح لأي حد لسكربتات شخصية
    # بدون الحاجة لتسجيل تطبيق منفصل. لو عندك App ID خاص بيك حطه هنا بدل.
    DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")
    DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Trading
    SYMBOL = os.getenv("SYMBOL", "XAUUSD")
    HTF_BIAS = os.getenv("HTF_BIAS", "H4")
    MTF_ZONE = os.getenv("MTF_ZONE", "H1")
    LTF_CONFIRM = os.getenv("LTF_CONFIRM", "M5")
    ENTRY_TF = os.getenv("ENTRY_TF", "M1")

    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))

    @classmethod
    def validate(cls):
        missing = []
        if not cls.DERIV_API_TOKEN:
            missing.append("DERIV_API_TOKEN")
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise ValueError(
                f"الإعدادات الناقصة في .env: {', '.join(missing)}"
            )
