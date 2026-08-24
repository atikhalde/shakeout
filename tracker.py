#!/usr/bin/env python3
"""
Signal tracking sheet: every alert is logged to a CSV, then marked HIT/MISS
~5 sessions later (exit discipline: the pattern is a 3-7 day bounce, and the
backtest shows holding longer fades).

Columns:
    symbol, signal_date, score, entry, stop, target, rr, vol_surge,
    status (OPEN/HIT/MISS), exit_date, r5, logged_at

Usage (auto-wired into the scanner):
    scanner.py logs every signal + updates OPEN rows each run.
    python tracker.py --report   -> print the current sheet summary
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os

FIELDS = ["symbol", "signal_date", "score", "entry", "stop", "target", "rr",
          "vol_surge", "status", "exit_date", "r5", "logged_at"]


def read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:  # noqa: BLE001
        return []


def write(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def last_alert_date(symbol: str, path: str = "signals_tracker.csv"):
    """Newest logged signal_date (ISO str) for `symbol`, or None."""
    best = None
    for r in read(path):
        if r.get("symbol") == symbol:
            d = str(r.get("signal_date", ""))[:10]
            if d and (best is None or d > best):
                best = d
    return best


def recently_alerted(symbol: str, as_of: str, days: int = 15,
                     path: str = "signals_tracker.csv") -> bool:
    """
    True if `symbol` was already alerted within `days` calendar days of
    `as_of` (YYYY-MM-DD). This is the CROSS-RUN cooldown: without it the
    same setup re-alerts on every scanner run that still sees the same
    last bar (e.g. 5 identical alerts on 2026-08-19 for the 2026-08-18
    signal), which makes repeats look like noise and silence look like a bug.
    """
    last = last_alert_date(symbol, path)
    if last is None:
        return False
    try:
        gap = (dt.date.fromisoformat(as_of[:10])
               - dt.date.fromisoformat(last)).days
    except ValueError:
        return False
    return 0 <= gap <= days


def log_signal(sig: dict, path: str = "signals_tracker.csv") -> bool:
    """Append a signal (deduped by symbol+date). Returns True if added."""
    rows = read(path)
    sym = sig.get("symbol", "?")
    date = str(sig.get("signal_date", ""))[:10]
    if any(r["symbol"] == sym and r["signal_date"] == date for r in rows):
        return False
    rows.append({
        "symbol": sym, "signal_date": date,
        "score": round(sig.get("score", 0), 1),
        "entry": round(sig.get("last_close", 0), 2),
        "stop": round(sig.get("stop_level", 0), 2),
        "target": round(sig.get("target_level", 0), 2),
        "rr": round(sig.get("rr", 0), 2),
        "vol_surge": round(sig.get("vol_surge", 0), 2),
        "status": "OPEN", "exit_date": "", "r5": "",
        "logged_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    write(path, rows)
    return True


def update_open(path: str, client, n_days: int = 5) -> int:
    """
    For every OPEN row with >= n_days of sessions passed, mark HIT/MISS
    using the actual close[n_days] vs entry. Returns # rows updated.
    """
    rows = read(path)
    changed = 0
    for r in rows:
        if r["status"] != "OPEN":
            continue
        try:
            d0 = dt.date.fromisoformat(r["signal_date"])
        except ValueError:
            continue
        bars = client.get_daily(r["symbol"], d0, dt.date.today())
        if bars is None or not bars.get("dates"):
            continue
        dates = [str(x)[:10] for x in bars["dates"]]
        if r["signal_date"] not in dates:
            continue
        i = dates.index(r["signal_date"])
        if i + n_days >= len(dates):
            continue  # not enough sessions yet - stays OPEN
        entry = float(r["entry"])
        if entry <= 0:
            continue
        r5 = (float(bars["close"][i + n_days]) / entry - 1) * 100
        r["status"] = "HIT" if r5 > 0 else "MISS"
        r["exit_date"] = dates[i + n_days]
        r["r5"] = round(r5, 2)
        changed += 1
    if changed:
        write(path, rows)
    return changed


def report(path: str) -> None:
    rows = read(path)
    if not rows:
        print("tracker: empty")
        return
    open_n = sum(1 for r in rows if r["status"] == "OPEN")
    done = [r for r in rows if r["status"] != "OPEN"]
    hits = sum(1 for r in done if r["status"] == "HIT")
    print(f"tracker: {len(rows)} signals "
          f"({open_n} OPEN, {len(done)} closed: {hits} HIT / "
          f"{len(done)-hits} MISS)")
    if done:
        avg = sum(float(r["r5"]) for r in done) / len(done)
        print(f"  closed avg r5 = {avg:+.2f}%  (win rate "
              f"{hits/len(done)*100:.0f}%)")
    for r in rows[-10:]:
        print(f"  {r['symbol']:12s} {r['signal_date']} score={r['score']:5} "
              f"status={r['status']:<4} r5={r['r5']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="signals_tracker.csv")
    ap.add_argument("--report", action="store_true", help="print the sheet")
    args = ap.parse_args()
    if args.report:
        report(args.file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
