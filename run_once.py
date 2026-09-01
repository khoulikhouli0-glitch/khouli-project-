from config import Config
from signal_engine import analyze_market
import telegram_notifier as notifier


def main():
    Config.validate()

    try:
        signal = analyze_market()
    except Exception as e:
        print(f"[Error] {e}")
        raise

    if signal:
        print(f"[Signal] {signal['direction']} @ {signal['entry']}")
        notifier.send_signal(signal)
    else:
        print("No trade opportunity right now.")


if __name__ == "__main__":
    main()
