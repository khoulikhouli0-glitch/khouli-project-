from config import Config
from signal_engine import analyze_market
import telegram_notifier as notifier


def main():
    Config.validate()

    try:
        signals = analyze_market(debug=True)
    except Exception as e:
        print(f"[Error] {e}")
        raise

    if signals:
        for signal in signals:
            print(f"[Signal] {signal['trade_label']} {signal['direction']} @ {signal['entry']} ({signal['confidence']})")
        notifier.send_signals(signals)
    else:
        print("No trade opportunity right now.")


if __name__ == "__main__":
    main()
