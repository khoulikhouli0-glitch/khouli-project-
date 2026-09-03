from config import Config
from backtest_engine import run_backtest


def main():
    Config.validate()
    run_backtest(debug=True)


if __name__ == "__main__":
    main()
