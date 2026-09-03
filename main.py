# نقطة التشغيل الرئيسية
# يفحص السوق كل POLL_INTERVAL_SECONDS، ولو فيه إشارات جديدة يبعتها على تلغرام
import time
import traceback
from datetime import datetime, timedelta

import deriv_connector as dc
import telegram_notifier as notifier
from config import Config
from signal_engine import analyze_market

COOLDOWN_MINUTES = 15
_last_sent = {}


def should_send(signal: dict) -> bool:
    key = (signal["trade_label"], signal["direction"])
    now = datetime.now()
    last_time = _last_sent.get(key)

    if last_time and now - last_time < timedelta(minutes=COOLDOWN_MINUTES):
        return False

    _last_sent[key] = now
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
                signals = analyze_market()
                to_send = [s for s in signals if should_send(s)]
                if to_send:
                    for s in to_send:
                        print(f"[Signal] {s['trade_label']} {s['direction']} @ {s['entry']} ({s['confidence']})")
                    notifier.send_signals(to_send)
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
