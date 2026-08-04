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
        "score_parts": {"BOS freshness": (20.0, 25.0), "Flush depth": (20.0, 20.0),
                        "SSL precision": (18.0, 20.0), "Reversal bounce": (12.0, 20.0),
                        "Candle body": (11.3, 15.0), "Trend (EMA20/50)": (0.0, 5.0)},
    }
    msg = TelegramNotifier.format_signal(sig)
    check("telegram msg contains symbol", "SPORTKING" in msg)
    check("telegram msg contains date", "2026-07-31" in msg)
    check("telegram msg contains SSL", "194.60" in msg)
    check("telegram msg is html", "<b>" in msg and "</b>" in msg)
    check("telegram msg has score breakdown", "what's rewarded" in msg
          and "Flush depth" in msg)
    # CRITICAL: no raw '<' that would break Telegram's HTML parser
    # (the old message had "still < peak" which caused HTTP 400)
    check("telegram html has no unescaped <", "< peak" not in msg
          and "still below peak" in msg)
    check("telegram msg is valid-ish html",
          msg.count("<") == msg.count(">") or "&lt;" in msg)

    # epoch timestamps should be rendered as dates
    sig2 = dict(sig, signal_date=1785695400, bos_date=1785522600)
    msg2 = TelegramNotifier.format_signal(sig2)
    check("telegram renders epoch as date", "2026-" in msg2
          and str(1785695400) not in msg2)

    summary = TelegramNotifier.format_summary(3)
    check("summary with signals", "3 signals" in summary)
    summary0 = TelegramNotifier.format_summary(0)
    check("summary without signals", "No pattern signals" in summary0)


def test_universe() -> None:
    print("== universe resolution (never hard-fails) ==")
    from universes import get_universe, FALLBACK_SYMBOLS

    syms, src = get_universe()  # no watchlist: dhan master or fallback
    check("universe never empty", len(syms) > 0, f"got {len(syms)}")
    check("fallback includes the demo stocks",
          all(s in FALLBACK_SYMBOLS for s in ("SPORTKING", "BAJFINANCE", "SPR_AUTO")))

    # watchlist path - explicit watchlist must win over any network source
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("# comment\nSPORTKING\n\nbajfinance\n")
        wl = f.name
    syms2, src2 = get_universe(watchlist=wl)
    os.unlink(wl)
    check("watchlist read", syms2 == ["SPORTKING", "BAJFINANCE"], f"{syms2}")
    check("watchlist source label", "watchlist" in src2)


def test_intraday() -> None:
    print("== intraday (live market) logic ==")
    import datetime as dt

    from dhan_client import DhanClient
    from scanner import merge_partial

    # ---- merge a partial candle onto daily history ----
    bars = {
        "dates": ["2026-07-30"], "open": [200.0], "high": [210.0],
        "low": [195.0], "close": [201.4], "volume": [2_600_000],
    }
    partial = {"open": 220.0, "high": 230.0, "low": 219.0,
               "close": 225.0, "volume": 800_000}
    merged, used = merge_partial(bars, partial, "2026-07-31")
    check("merge appends today", merged["dates"][-1] == "2026-07-31")
    check("merge sets partial_last", merged.get("partial_last") is True)
    check("merge OHLC", merged["close"][-1] == 225.0 and merged["high"][-1] == 230.0)

    bars2 = {"dates": ["2026-07-31"], "open": [200.0], "high": [210.0],
             "low": [195.0], "close": [201.4], "volume": [2_600_000]}
    m2, used2 = merge_partial(bars2, partial, "2026-07-31")
    check("no duplicate when today already present",
          used2 is False and len(m2["dates"]) == 1)

    # ---- intraday payload parsing (list-of-lists format) ----
    class FakeResp:
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    client = DhanClient("fake", min_interval=0.0)
    # resolve_symbol uses the bundled repo map (no network), so use a real
    # symbol from the map and stub _post for the intraday payload
    client._post = lambda path, payload: FakeResp([
        [1750000000000, 100.0, 105.0, 99.0, 102.0, 1000],
        [1750000060000, 102.0, 106.0, 100.0, 104.0, 1200],
    ])
    rows = client.get_intraday("RELIANCE", dt.date(2026, 7, 31))
    check("intraday rows parsed", rows is not None and len(rows) == 2)
    check("intraday row ohlc", rows[1]["high"] == 106.0 and rows[1]["close"] == 104.0)

    client._post = lambda path, payload: FakeResp([
        [1750000000000, 100.0, 105.0, 99.0, 102.0, 1000],
        [1750000060000, 102.0, 106.0, 100.0, 104.0, 1200],
    ])
    p = client.intraday_partial("RELIANCE", dt.date(2026, 7, 31))
    check("partial aggregated", p is not None and p["open"] == 100.0
          and p["high"] == 106.0 and p["low"] == 99.0
          and p["close"] == 104.0 and p["volume"] == 2200)

    client._post = lambda path, payload: FakeResp([])
    p2 = client.intraday_partial("RELIANCE", dt.date(2026, 8, 1))
    check("no data -> None", p2 is None)

    # ---- get_instruments works from the bundled repo map (no network) ----
    m = client.get_instruments()
    check("instruments from repo map", len(m) > 2000, f"got {len(m)}")
    check("symbol alias resolves", client.resolve_symbol("SPR_AUTO") is not None)

    # ---- ETF exclusion: real stocks kept, funds removed ----
    liq = client.liquid_universe()
    for s in ("RELIANCE", "SPORTKING", "BAJFINANCE", "SHRIPISTON",
              "GOLDIAM", "JETFREIGHT", "ALPHAGEO", "MIDHANI"):
        check(f"ETF filter keeps {s}", s in liq)
    for s in ("NIFTYBEES", "GOLDBEES", "BANKBEES", "SETFNIF50",
              "TATSILV", "INFRA", "METAL", "VALUE", "NIFTY50",
              "GROWWNIFTY", "LIQUIDBEES"):
        check(f"ETF filter drops {s}", s not in liq)

    # ---- timestamp -> ISO date conversion ----
    from dhan_client import _iso_date
    check("epoch ms -> date", _iso_date(1785695400000) == "2026-08-02")
    check("epoch s -> date", _iso_date(1785695400) == "2026-08-02")
    check("iso passthrough", _iso_date("2026-07-31") == "2026-07-31")


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
    test_intraday()
    test_universe()
    test_positives()
    test_negatives()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
