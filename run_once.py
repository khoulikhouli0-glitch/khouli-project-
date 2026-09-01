""
سكربت "تشغيل مرة واحدة" - مخصص للعمل مع GitHub Actions
(بدل main.py اللي فيه حلقة لا نهائية، مش مناسبة لبيئة زي GitHub Actions
اللي بتشغل الكود، تنفذه مرة، وتقفله).

كل مرة GitHub Actions يشغّل السكربت ده (كل 15 دقيقة حسب الجدولة)،
بيفحص السوق مرة واحدة، ولو فيه فرصة يبعتها على تلغرام، وبعدين يخلص.
"""
from config import Config
from signal_engine import analyze_market
import telegram_notifier as notifier


def main():
    Config.validate()

    notifier.send_text("اختبار: الـ Agent شغال ومتصل بتلغرام بنجاح")

    try:
        signal = analyze_market()
    except Exception as e:
        print(f"[Error] حدث خطأ أثناء التحليل: {e}")
        raise

    if signal:
        print(f"[Signal] {signal['direction']} @ {signal['entry']}")
        notifier.send_signal(signal)
    else:
        print("لا توجد فرصة تداول حالياً.")


if __name__ == "__main__":
    main()
