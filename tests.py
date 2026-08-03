#!/usr/bin/env python3
"""
Test suite for the BOS->flush->SSL-retest scanner.

Run:  python -m pattern_scanner.tests   (or: python tests.py)
Exit code 0 = all pass, 1 = failures.
"""

from __future__ import annotations

import sys

import numpy as np

from config import ScanConfig
from demo_data import demo_universe
from indicators import ema, rolling_max
from pattern import detect_setup
from telegram_notifier import TelegramNotifier

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def test_positives() -> None:
    print("== positive cases (must flag on the expected date) ==")
    cfg = ScanConfig()
    uni = demo_universe()
    for sym in ("SPORTKING", "BAJFINANCE", "SPR_AUTO"):
        dates, bars, expected = uni[sym]
        sig = detect_setup(bars, dates, cfg)
        check(f"{sym} detected",
              sig is not None,
              f"got {sig['signal_date'] if sig else None}")
        if sig:
            check(f"{sym} flagged on {expected}",
                  sig["signal_date"] == expected,
                  f"got {sig['signal_date']}")
            check(f"{sym} score >= threshold",
                  sig["score"] >= cfg.score_threshold,
                  f"score {sig['score']}")
            # sanity of the anatomy
            check(f"{sym} flush below peak",
                  sig["flush_low"] < sig["peak"])
            check(f"{sym} closes held above SSL",
                  sig["min_close_after_ssl"] > sig["ssl"])
            check(f"{sym} still below peak (before big move)",
                  sig["last_close"] < sig["peak"])


def test_telegram_format() -> None:
    print("== telegram message formatting ==")
    sig = {
        "symbol": "SPORTKING", "score": 81.3, "signal_date": "2026-07-31",
        "last_close": 201.44, "bos_date": "2026-07-29", "bos_style": "swing",
        "break_level": 218.69, "peak": 219.03, "peak_date": "2026-07-29",
        "flush_drop_pct": 11.0, "flush_low": 194.99, "flush_date": "2026-07-30",
        "ssl": 194.6, "min_close_after_ssl": 196.5, "bounce_pct": 2.5,
        "body_ratio": 0.79,
    }
    msg = TelegramNotifier.format_signal(sig)
    check("telegram msg contains symbol", "SPORTKING" in msg)
    check("telegram msg contains date", "2026-07-31" in msg)
    check("telegram msg contains SSL", "194.60" in msg)
    check("telegram msg is html", "<b>" in msg and "</b>" in msg)

    summary = TelegramNotifier.format_summary(3)
    check("summary with signals", "3 signals" in summary)
    summary0 = TelegramNotifier.format_summary(0)
    check("summary without signals", "No pattern signals" in summary0)


def test_negatives() -> None:
    print("== negative cases (must NOT flag) ==")
    cfg = ScanConfig()
    uni = demo_universe()
    for sym in ("NEG_RUNNER", "NEG_SSL_BREAK", "NEG_NO_BOS", "NEG_POST_MOVE",
                "NEG_STILL_FALLING", "NEG_SHALLOW", "NEG_STALE"):
        dates, bars, expected = uni[sym]
        sig = detect_setup(bars, dates, cfg)
        check(f"{sym} rejected", sig is None,
              f"unexpected signal {sig['signal_date'] if sig else ''} "
              f"score={sig['score'] if sig else ''}")


def test_indicators() -> None:
    print("== indicator sanity ==")
    x = np.arange(1.0, 11.0)
    check("ema length", len(ema(x, 5)) == 10)
    const = np.full(10, 7.0)
    check("ema of constant is constant", np.allclose(ema(const, 5), 7.0))
    check("rolling_max length", len(rolling_max(x, 3)) == 10)
    check("rolling_max value",
          rolling_max(np.array([1.0, 5.0, 3.0, 2.0, 4.0]), 3)[-1] == 4.0)
    check("rolling_max early nan",
          np.isnan(rolling_max(np.array([1.0, 2.0]), 3)).all())


def main() -> int:
    global PASS, FAIL
    test_indicators()
    test_telegram_format()
    test_positives()
    test_negatives()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
