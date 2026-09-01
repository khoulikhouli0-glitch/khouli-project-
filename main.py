"""
نقطة التشغيل الرئيسية.
يفحص السوق كل POLL_INTERVAL_SECONDS، ولو فيه إشارة جديدة يبعتها على تلغرام.
"""
import time
import traceback
from datetime import datetime, timedelta

import deriv_connector as dc
import telegram_notifier as notifier
from config import Config
from signal_engine import analyze_market

# لمنع تكرار نفس الإشارة كل شوية
COOLDOWN_MINUTES = 15
_last_signal_time = None
_last_signal_direction = None


def should_send(signal: dict) -> bool:
    global _last_signal_time, _last_signal_direction

    if signal is None:
        return False

    now = datetime.now()
    if (
        _last_signal_time
        and _last_signal_direction == signal["direction"]
        and now - _last_signal_time < timedelta(minutes=COOLDOWN_MINUTES)
    ):
        return False

    _last_signal_time = now
    _last_signal_direction = signal["direction"]
    return True


def main():
    Config.validate()

    if not dc.connect():
        print("فشل الاتصال بـ Deriv. تأكد من صحة DERIV_API_TOKEN في .env")
        return

    notifier.send_text(
        f"✅ تم تشغيل agent تحليل الذهب (SMC)\nالرمز: {Config.SYMBOL}\nكل {Config.POLL_INTERVAL_SECONDS} ثانية سيتم فحص السوق."
    )

    print("[Agent] بدأ العمل... اضغط Ctrl+C للإيقاف.")

    try:
        while True:
            try:
                signal = analyze_market()
                if should_send(signal):
                    print(f"[Signal] {signal['direction']} @ {signal['entry']}")
                    notifier.send_signal(signal)
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] لا توجد فرصة حالياً...")
            except Exception as e:
                print(f"[Error] حدث خطأ أثناء التحليل: {e}")
                traceback.print_exc()

            time.sleep(Config.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\n[Agent] تم الإيقاف يدوياً.")
    finally:
        dc.disconnect()


if __name__ == "__main__":
    main()
