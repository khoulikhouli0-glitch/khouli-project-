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
        return "stop invalidated"
    if "R:R" in msg and "below minimum" in msg:
        return "R:R below minimum"
    if "SIGNAL" in msg:
        return "SIGNAL GENERATED"
    return "other/unrecognized"


def run_backtest(debug: bool = False) -> dict:
    print("[Backtest] Fetching historical data...")
    daily_df = dc.get_historical_candles("D1", months=MONTHS_MAIN)
    h4_df = dc.get_historical_candles("H4", months=MONTHS_MAIN)
    h1_df = dc.get_historical_candles("H1", months=MONTHS_MAIN)
    m15_df = dc.get_historical_candles("M15", months=MONTHS_MAIN)
    m5_df = dc.get_historical_candles("M5", months=MONTHS_M5)
    print(f"[Backtest] Daily={len(daily_df)} H4={len(h4_df)} H1={len(h1_df)} M15={len(m15_df)} M5={len(m5_df)}")

    daily_times = daily_df["time"]
    h4_times = h4_df["time"]
    h1_times = h1_df["time"]
    m5_times = m5_df["time"]

    open_positions = {}
    closed_trades = []
    skip_reasons = {
        "Swing (Daily)": Counter(),
        "Swing (H4)": Counter(),
        "Scalp (H1)": Counter(),
    }
    total_steps = len(m15_df)
    diagnostic_done = False

    for i in range(total_steps):
        current_row = m15_df.iloc[i]
        current_time = current_row["time"]

        for label in list(open_positions.keys()):
            position = open_positions[label]
            if position is None:
                continue

            outcome = _check_exit(position, current_row)
            expired = (current_time - position["opened_at"]) > position["max_hold"]

            if outcome is not None:
                closed_trades.append({
                    "trade_label": label,
                    "direction": position["direction"],
                    "outcome": outcome,
                    "r_multiple": _r_multiple(position, outcome),
                    "opened_at": position["opened_at"],
                    "closed_at": current_time,
                })
                open_positions[label] = None
            elif expired:
                closed_trades.append({
                    "trade_label": label,
                    "direction": position["direction"],
                    "outcome": "expired",
                    "r_multiple": 0.0,
                    "opened_at": position["opened_at"],
                    "closed_at": current_time,
                })
                open_positions[label] = None

        busy_count = sum(1 for v in open_positions.values() if v is not None)
        if busy_count < 3:
            d_idx = daily_times.searchsorted(current_time, side="right")
            h4_idx = h4_times.searchsorted(current_time, side="right")
            h1_idx = h1_times.searchsorted(current_time, side="right")
            m5_idx = m5_times.searchsorted(current_time, side="right")

            daily_slice = daily_df.iloc[max(0, d_idx - DAILY_WINDOW): d_idx].reset_index(drop=True)
            h4_slice = h4_df.iloc[max(0, h4_idx - H4_WINDOW): h4_idx].reset_index(drop=True)
            h1_slice = h1_df.iloc[max(0, h1_idx - H1_WINDOW): h1_idx].reset_index(drop=True)
            m15_slice = m15_df.iloc[max(0, i + 1 - M15_WINDOW): i + 1].reset_index(drop=True)
            m5_slice = (
                m5_df.iloc[max(0, m5_idx - M5_WINDOW): m5_idx].reset_index(drop=True)
                if m5_idx > 0 else None
            )

            if len(daily_slice) >= 20 and len(h4_slice) >= 20 and len(h1_slice) >= 20:

                if not diagnostic_done:
                    diagnostic_done = True
                    from smartmoneyconcepts import smc as _smc_diag
                    print("\n===== ONE-TIME DIAGNOSTIC (Daily slice) =====")
                    print(f"daily_slice len={len(daily_slice)}  columns={list(daily_slice.columns)}")
                    print(f"daily_slice index type={type(daily_slice.index)}  first idx={daily_slice.index[0]}  last idx={daily_slice.index[-1]}")
                    try:
                        sw_diag = _smc_diag.swing_highs_lows(daily_slice, swing_length=signal_engine.BIAS_SWING_LENGTH)
                        print(f"swing_highs_lows OK. non-null HighLow count = {sw_diag['HighLow'].notna().sum()}")
                        bc_diag = _smc_diag.bos_choch(daily_slice, sw_diag, close_break=True)
                        print(f"bos_choch OK. non-null BOS = {bc_diag['BOS'].notna().sum()}  non-null CHOCH = {bc_diag['CHOCH'].notna().sum()}")
                        print(f"BOS nonzero values: {bc_diag['BOS'].dropna().unique().tolist()}")
                        print(f"CHOCH nonzero values: {bc_diag['CHOCH'].dropna().unique().tolist()}")
                    except Exception as e:
                        print(f"EXCEPTION during diagnostic: {type(e).__name__}: {e}")
                    print("===== END DIAGNOSTIC =====\n")

                captured = []
                orig_process = signal_engine._process_path

                def wrapped_process(*args, **kwargs):
                    sig, msg = orig_process(*args, **kwargs)
                    captured.append(msg)
                    return sig, msg

                signal_engine._process_path = wrapped_process
                signals = signal_engine.analyze_market_from_data(
                    daily_slice, h4_slice, h1_slice, m15_slice, m5_slice, debug=False
                )
                signal_engine._process_path = orig_process

                path_order = ["Swing (Daily)", "Swing (H4)", "Scalp (H1)"]
                for idx, msg in enumerate(captured):
                    if idx < len(path_order):
                        label = path_order[idx]
                        skip_reasons[label][_classify_skip(msg)] += 1

                for sig in signals:
                    label = sig["trade_label"]
                    if open_positions.get(label) is None:
                        open_positions[label] = {
                            "direction": sig["direction"],
                            "entry": sig["entry"],
                            "stop_loss": sig["stop_loss"],
                            "take_profit": sig["take_profit"],
                            "opened_at": current_time,
                            "max_hold": MAX_HOLD[label],
                        }

        if debug and i % 500 == 0:
            print(f"[Backtest] step {i}/{total_steps}  time={current_time}")

    report = _build_report(closed_trades)
    _print_report(report, closed_trades, skip_reasons)
    return report
