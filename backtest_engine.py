from datetime import timedelta
from collections import Counter

import deriv_connector as dc
import signal_engine

MONTHS_MAIN = 1
MONTHS_M5 = 0.5

DAILY_WINDOW = 120
H4_WINDOW = 120
H1_WINDOW = 150
M15_WINDOW = 150
M5_WINDOW = 100

MAX_HOLD = {
    "Swing (Daily)": timedelta(days=10),
    "Swing (H4)": timedelta(days=5),
    "Scalp (H1)": timedelta(days=1),
}


def _check_exit(position, candle):
    direction = position["direction"]
    high = candle["high"]
    low = candle["low"]

    if direction == "BUY":
        hit_sl = low <= position["stop_loss"]
        hit_tp = high >= position["take_profit"]
    else:
        hit_sl = high >= position["stop_loss"]
        hit_tp = low <= position["take_profit"]

    if hit_sl:
        return "loss"
    if hit_tp:
        return "win"
    return None


def _r_multiple(position, outcome):
    risk = abs(position["entry"] - position["stop_loss"])
    if risk == 0:
        return 0.0
    if outcome == "win":
        reward = abs(position["take_profit"] - position["entry"])
        return round(reward / risk, 2)
    if outcome == "loss":
        return -1.0
    return 0.0


def _summarize(trades):
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    expired = [t for t in trades if t["outcome"] == "expired"]
    decided = wins + losses

    win_rate = round(len(wins) / len(decided) * 100, 1) if decided else None
    avg_r = round(sum(t["r_multiple"] for t in decided) / len(decided), 2) if decided else None
    total_r = round(sum(t["r_multiple"] for t in decided), 2) if decided else 0.0

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "expired": len(expired),
        "win_rate_pct": win_rate,
        "avg_r": avg_r,
        "total_r": total_r,
    }


def _build_report(closed_trades):
    report = {"by_path": {}, "overall": {}}

    for label in ("Swing (Daily)", "Swing (H4)", "Scalp (H1)"):
        path_trades = [t for t in closed_trades if t["trade_label"] == label]
        report["by_path"][label] = _summarize(path_trades)

    report["overall"] = _summarize(closed_trades)
    return report


def _print_report(report, closed_trades, skip_reasons):
    print("\n========== BACKTEST REPORT ==========")
    print(f"Total closed trades: {len(closed_trades)}")

    print("\n--- By Path ---")
    for label, stats in report["by_path"].items():
        print(
            f"{label}: total={stats['total']} win={stats['wins']} loss={stats['losses']} "
            f"expired={stats['expired']} win_rate={stats['win_rate_pct']}% "
            f"avg_R={stats['avg_r']} total_R={stats['total_r']}"
        )

    print("\n--- Overall ---")
    o = report["overall"]
    print(
        f"total={o['total']} win={o['wins']} loss={o['losses']} expired={o['expired']} "
        f"win_rate={o['win_rate_pct']}% avg_R={o['avg_r']} total_R={o['total_r']}"
    )

    print("\n--- Skip Reason Tally (why no signal fired) ---")
    for path_label, counter in skip_reasons.items():
        print(f"\n{path_label}:")
        for reason, count in counter.most_common():
            print(f"  {count:6d}  {reason}")

    print("======================================\n")


def _classify_skip(msg: str) -> str:
    if "no respected BOS/CHoCH bias" in msg:
        return "no bias"
    if "price in" in msg and "zone, need" in msg:
        return "premium/discount mismatch"
    if "no respected trigger" in msg or "no respected matching" in msg:
        return "no fresh trigger (BOS/CHoCH)"
    if "no liquidity target" in msg:
        return "no liquidity target"
    if "not in any entry array" in msg:
        return "price not in entry array (OB/FVG/OTE)"
    if "stop already invalidated" in msg:
