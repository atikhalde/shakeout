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
from demo_data import demo_universe, _dates_ending, _pre_history
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
            # trade plan must be a valid LONG: stop < entry < target, rr>0
            # (regression: target used to be anchored to the SSL zone, so a
            #  close above SSL*1.08 gave target < entry and NEGATIVE R:R)
            tp_ok = (sig["stop_level"] < sig["last_close"]
                     < sig["target_level"] and sig["rr"] > 0)
            check(f"{sym} trade plan sane (stop<entry<target, rr>0)",
                  tp_ok,
                  f"stop={sig['stop_level']:.2f} entry={sig['last_close']:.2f} "
                  f"target={sig['target_level']:.2f} rr={sig['rr']:.2f}")


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
    check("telegram msg has trade plan", "TRADE PLAN" in msg
          and "Entry" in msg and "Stop" in msg and "R:R" in msg)
    check("telegram msg has volume info", "volume" in msg.lower())
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

    # regression: the user's real alert (close 1130.60 / SSL 999.63) used to
    # render "Target ₹1079.61 (+-4.5%) · R:R -0.39" because the target was
    # anchored to the SSL zone (999.63*1.08=1079.61 < entry). It must now
    # be entry-based: target = close*1.08 = 1221.05, positive R:R.
    sig_user = dict(sig, symbol="USERX", score=74.0, signal_date="2026-08-18",
                    last_close=1130.60, ssl=999.63, stop_level=999.63,
                    target_level=round(1130.60 * 1.08, 2),
                    rr=(1130.60 * 1.08 - 1130.60) / (1130.60 - 999.63))
    msg_user = TelegramNotifier.format_signal(sig_user)
    check("trade plan target above entry",
          "Target ₹1221.05 (+8.0%)" in msg_user, msg_user)
    check("trade plan R:R positive", "R:R 0.69" in msg_user, msg_user)
    check("trade plan has no '+-' sign glitch", "+-" not in msg_user)
    check("trade plan stop below entry",
          "Stop ₹999.63 (−11.6%)" in msg_user, msg_user)
    check("alert still names the stock", "USERX" in msg_user)

    summary = TelegramNotifier.format_summary(3)
    check("summary with signals", "3 signals" in summary)
    summary0 = TelegramNotifier.format_summary(0)
    check("summary without signals", "No pattern signals" in summary0)


def test_symbol_in_alerts() -> None:
    """Regression: alerts must always carry the stock symbol. The Dhan
    cache-read path and the yfinance fallback used to drop the 'symbol'
    key, so live alerts could arrive as 'PATTERN SIGNAL — ?'."""
    print("== symbol always present (cache read / yfinance fallback) ==")
    import datetime as dt
    import os
    import tempfile
    import unittest.mock as mock

    from dhan_client import DhanClient
    import scanner as scanner_mod

    # ---- Dhan cache path: the symbol must be restored on cache reads ----
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "RELIANCE.csv"), "w", newline="") as f:
        f.write("date,open,high,low,close,volume\n")
        for i in range(1, 6):
            f.write(f"2026-08-0{i},100,105,99,102,1000\n")
    client = DhanClient("fake", cache_dir=tmp, min_interval=0.0)
    bars = client.get_daily("RELIANCE", dt.date(2026, 8, 1), dt.date(2026, 8, 5))
    check("cache read keeps symbol",
          bars is not None and bars.get("symbol") == "RELIANCE",
          f"symbol={bars.get('symbol') if bars else None}")

    # ---- yfinance fallback: the payload must include the symbol ----
    import pandas as pd
    idx = pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"])
    df = pd.DataFrame({
        "Open": [100.0, 102.0, 101.0], "High": [105.0, 106.0, 104.0],
        "Low": [99.0, 100.0, 99.0], "Close": [102.0, 104.0, 103.0],
        "Volume": [1000, 1100, 900],
    }, index=idx)
    with mock.patch("yfinance.Ticker") as tk:
        tk.return_value.history.return_value = df
        got = scanner_mod._yf_daily("SPORTKING", dt.date(2026, 7, 1),
                                    dt.date(2026, 8, 5))
    check("yfinance fallback keeps symbol",
          got is not None and got.get("symbol") == "SPORTKING",
          f"symbol={got.get('symbol') if got else None}")


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

    # ---- REGRESSION: bars from the yfinance fallback are numpy arrays,
    #      not lists - merge_partial crashed with KeyError: 'open' and
    #      took down every intraday GitHub Actions run (exit code 1) ----
    import numpy as _np
    bars_np = {
        "symbol": "SPORTKING",
        "dates": ["2026-07-29", "2026-07-30"],
        "open": _np.array([198.0, 200.0]),
        "high": _np.array([205.0, 210.0]),
        "low": _np.array([196.0, 195.0]),
        "close": _np.array([199.0, 201.4]),
        "volume": _np.array([2_100_000.0, 2_600_000.0]),
    }
    try:
        m3, used3 = merge_partial(bars_np, partial, "2026-07-31")
        check("numpy bars merge without crash", used3 is True)
        check("numpy bars appended OHLC",
              m3["close"][-1] == 225.0 and m3["dates"][-1] == "2026-07-31")
        check("numpy bars keep the symbol", m3.get("symbol") == "SPORTKING")
    except Exception as e:  # noqa: BLE001
        check("numpy bars merge without crash", False, f"{type(e).__name__}: {e}")

    # ---- REGRESSION: merging must not drop scalar keys like 'symbol'
    #      (dropping it re-broke the "PATTERN SIGNAL — ?" alert bug) ----
    bars_sym = {
        "symbol": "BAJFINANCE",
        "dates": ["2026-07-30"], "open": [200.0], "high": [210.0],
        "low": [195.0], "close": [201.4], "volume": [2_600_000],
    }
    m4, used4 = merge_partial(bars_sym, partial, "2026-07-31")
    check("list bars keep the symbol",
          used4 is True and m4.get("symbol") == "BAJFINANCE")

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


def test_aci_26w_proximity() -> None:
    """ACI (real data) broke only a 45-day swing high - never reached its
    26-week high (94.9%) -> must be rejected by the proximity guard."""
    print("== ACI 26-week-high proximity (real data regression) ==")
    import os
    from pattern import detect_setup
    from config import ScanConfig

    # build a synthetic ACI-like series: old high 636 (26W), rally peaks at
    # ~604 (95%), flush to ~518 shelf, bounce - but peak never >= 97% of 26W
    rng = np.random.default_rng(123)
    n = 200
    # old 26W high ~636 made 40 bars ago, then decline to ~520 base
    seg1 = _pre_history(80, 500, 636, rng, 0.012, max_cap=637)
    seg2 = _pre_history(80, 636, 520, rng, 0.014, max_cap=637)
    seg3 = _pre_history(40, 520, 560, rng, 0.010)   # rally but capped below 636*0.97
    o = np.concatenate([seg1[0], seg2[0], seg3[0]])
    h = np.concatenate([seg1[1], seg2[1], seg3[1]])
    l = np.concatenate([seg1[2], seg2[2], seg3[2]])
    c = np.concatenate([seg1[3], seg2[3], seg3[3]])
    v = np.concatenate([seg1[4], seg2[4], seg3[4]])
    dates = _dates_ending("2026-08-04", n)
    bars = {"open": o, "high": h, "low": l, "close": c,
            "volume": v, "symbol": "ACI_LIKE"}

    sig = detect_setup(bars, dates, ScanConfig())
    check("ACI-like rejected (peak < 97% of 26W high)",
          sig is None, f"unexpected signal {sig['signal_date'] if sig else ''}")

    # the 3 verified positives still pass
    from demo_data import demo_universe
    uni = demo_universe()
    for s in ("SPORTKING", "BAJFINANCE", "SPR_AUTO"):
        d, b, exp = uni[s]
        sig = detect_setup(b, d, ScanConfig())
        check(f"{s} still flagged with proximity guard",
              sig is not None and sig["signal_date"] == exp)


def test_run_live_unpack() -> None:
    """Regression: run_live's scan_one must always return 3-tuples
    (the GitHub runner crashed with 'expected 3, got 2')."""
    print("== run_live end-to-end (mocked Dhan, exercises unpack) ==")
    import os
    import tempfile
    import types
    import unittest.mock as mock
    import scanner as scanner_mod

    # fake client: instruments + daily bars (returns the demo SPORTKING tail)
    from demo_data import demo_universe
    _dates, _bars, _exp = demo_universe()["SPORTKING"]
    _bars = {k: list(v) for k, v in _bars.items()}
    _bars["dates"] = list(_dates)

    class FakeClient:
        def __init__(self, *a, **k): pass
        def get_instruments(self):
            return {"SPORTKING": "1", "BAJFINANCE": "2", "SPR_AUTO": "3"}
        def liquid_universe(self):
            return ["SPORTKING", "BAJFINANCE", "SPR_AUTO"]
        def resolve_symbol(self, s): return "1"
        def get_daily(self, sym, *a, **k):
            # deliberately DROP the symbol (like a cache read / yfinance
            # fallback would) - scanner must restore it before alerting
            b = {kk: list(vv) for kk, vv in _bars.items()
                 if kk != "symbol"}
            return b
        def intraday_partial(self, *a, **k): return None

    import dhan_client as dhan_mod
    with mock.patch.object(dhan_mod, "DhanClient", FakeClient):
        cfg = ScanConfig()
        # isolate: never let a repo-level signals_tracker.csv (from an
        # earlier run) suppress this test's signals via the cross-run
        # cooldown - the test asserts fresh-scan behavior
        cfg.tracker_file = os.path.join(tempfile.mkdtemp(), "tracker.csv")
        rows = scanner_mod.run_live(
            cfg, "tok", "cid", limit=0,
            watchlist="watchlist.txt", from_days=400,
            force_refresh=False, debug=False, intraday=False,
            notifier=None,
        )
    check("run_live completes without crash", rows is not None)
    check("run_live returns a list", isinstance(rows, list))
    check("run_live signals carry the symbol (not '?')",
          rows and all(r.get("symbol") and r["symbol"] != "?"
                       for r in rows),
          f"symbols={[r.get('symbol') for r in rows] if rows else 'none'}")


def test_cross_run_cooldown_and_summary() -> None:
    """
    Regression (2026-08-19/20): the SAME setup re-alerted on every scanner
    run that still served the same last daily bar (5 identical '? ' alerts
    on Aug 19 for the Aug-18 signal). The tracker-backed cross-run cooldown
    must suppress a symbol re-alerted within cooldown_days, and the Telegram
    summary must be able to carry scan stats / near-misses.
    """
    print("== cross-run alert cooldown + summary stats ==")
    import os
    import tempfile
    import unittest.mock as mock
    import scanner as scanner_mod
    import dhan_client as dhan_mod
    from tracker import log_signal, recently_alerted, last_alert_date
    from telegram_notifier import TelegramNotifier

    # ---- unit: recently_alerted semantics ----
    f = os.path.join(tempfile.mkdtemp(), "tracker.csv")
    log_signal({"symbol": "MYSTOCK", "signal_date": "2026-08-18",
                "score": 74, "last_close": 1130.6, "stop_level": 999.63,
                "target_level": 1221.05, "rr": 0.69, "vol_surge": 1.36}, f)
    check("last_alert_date finds newest", last_alert_date("MYSTOCK", f) == "2026-08-18")
    check("same-day repeat suppressed",
          recently_alerted("MYSTOCK", "2026-08-18", 15, f) is True)
    check("next-day repeat suppressed",
          recently_alerted("MYSTOCK", "2026-08-19", 15, f) is True)
    check("day-15 repeat suppressed",
          recently_alerted("MYSTOCK", "2026-09-02", 15, f) is True)
    check("day-16 repeat allowed",
          recently_alerted("MYSTOCK", "2026-09-03", 15, f) is False)
    check("other symbol unaffected",
          recently_alerted("OTHER", "2026-08-19", 15, f) is False)

    # ---- end-to-end: run_live twice on the same bars -> 2nd run silent ----
    from demo_data import demo_universe
    _dates, _bars, _exp = demo_universe()["SPORTKING"]
    _bars = {k: list(v) for k, v in _bars.items()}
    _bars["dates"] = list(_dates)

    class FakeClient:
        def __init__(self, *a, **k): pass
        def get_instruments(self):
            return {"SPORTKING": "1", "BAJFINANCE": "2", "SPR_AUTO": "3"}
        def liquid_universe(self):
            return ["SPORTKING", "BAJFINANCE", "SPR_AUTO"]
        def resolve_symbol(self, s): return "1"
        def get_daily(self, sym, *a, **k):
            b = {kk: list(vv) for kk, vv in _bars.items() if kk != "symbol"}
            return b
        def intraday_partial(self, *a, **k): return None

    cfg = ScanConfig()
    cfg.tracker_file = os.path.join(tempfile.mkdtemp(), "signals_tracker.csv")
    with mock.patch.object(dhan_mod, "DhanClient", FakeClient):
        first = scanner_mod.run_live(cfg, "tok", "cid", limit=0,
                                     watchlist="watchlist.txt", from_days=400,
                                     force_refresh=False, debug=False,
                                     intraday=False, notifier=None)
        second = scanner_mod.run_live(cfg, "tok", "cid", limit=0,
                                      watchlist="watchlist.txt", from_days=400,
                                      force_refresh=False, debug=False,
                                      intraday=False, notifier=None)
    check("first run alerts the signals", len(first) == 3)
    check("second run on same bars is silent (cooldown)",
          len(second) == 0,
          f"second run returned {len(second)} signals")

    # ---- summary stats rendering ----
    s = TelegramNotifier.format_summary(
        0, "800 symbols",
        "scanned 800 · prefilter-skipped 640 · errors 3 · near-misses "
        "(score < 70): TATAPOWER 66, HAL 62")
    check("summary carries stats line", "near-misses" in s and "⚙️" in s)


def test_fail_fast_dhan_to_yfinance() -> None:
    """
    Regression (2026-08-24): a failing Dhan call used to sleep through
    1+2+3s of retry back-off BEFORE the scanner fell back to yfinance -
    ~6s wasted per symbol, ~80 min across an 800-symbol run with a dead
    token. Requirement: Dhan failure -> yfinance on the NEXT second.
    """
    print("== fail-fast: Dhan error -> instant yfinance fallback ==")
    import datetime as dt
    import os
    import tempfile
    import time as _time
    import unittest.mock as mock
    import requests
    import dhan_client as dhan_mod
    from dhan_client import DhanAuthError, DhanClient

    class FakeResp:
        def __init__(self, status): self.status_code = status; self.text = "x"
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    def client():
        return DhanClient("bad-token", cache_dir=tempfile.mkdtemp(),
                          min_interval=0.0, timeout=5.0)

    # ---- 401: raise DhanAuthError instantly, remember it, never re-call ----
    calls = {"n": 0}
    def post_401(*a, **k):
        calls["n"] += 1
        return FakeResp(401)
    c = client()
    with mock.patch.object(dhan_mod.requests, "post", post_401):
        t0 = _time.monotonic()
        try:
            c._post("/v2/charts/historical", {})
            raised = None
        except Exception as e:
            raised = e
        el = _time.monotonic() - t0
    check("401 raises DhanAuthError", isinstance(raised, DhanAuthError))
    check("401 fails fast (<1s, no back-off sleeps)", el < 1.0, f"{el:.2f}s")
    check("401 marks token dead", c.auth_dead is True)
    check("401 not retried", calls["n"] == 1, f"calls={calls['n']}")
    t0 = _time.monotonic()
    try:
        c._post("/v2/charts/historical", {})
    except DhanAuthError:
        pass
    check("later calls fail instantly (0 new HTTP)",
          calls["n"] == 1 and _time.monotonic() - t0 < 0.5)

    # ---- 429: raise immediately + short global pause, no waiting around ----
    calls["n"] = 0
    def post_429(*a, **k):
        calls["n"] += 1
        return FakeResp(429)
    c = client()
    with mock.patch.object(dhan_mod.requests, "post", post_429):
        t0 = _time.monotonic()
        try:
            c._post("/v2/charts/historical", {})
        except dhan_mod.DhanThrottledError:
            pass
        el = _time.monotonic() - t0
        check("429 raises immediately (<1s)", el < 1.0, f"{el:.2f}s")
        check("429 not retried", calls["n"] == 1, f"calls={calls['n']}")
        # during the cool-off window: instant raise, zero new requests
        t0 = _time.monotonic()
        try:
            c._post("/v2/charts/historical", {})
        except dhan_mod.DhanThrottledError as e:
            paused = "cooling off" in str(e)
        check("429 pause skips Dhan instantly",
              calls["n"] == 1 and _time.monotonic() - t0 < 0.5 and paused)

    # ---- timeout: ONE attempt only (never pay the timeout twice) ----
    calls["n"] = 0
    def post_timeout(*a, **k):
        calls["n"] += 1
        raise requests.Timeout("simulated hang")
    c = client()
    with mock.patch.object(dhan_mod.requests, "post", post_timeout):
        try:
            c._post("/v2/charts/historical", {})
        except requests.Timeout:
            pass
    check("timeout: single attempt, no retry", calls["n"] == 1,
          f"calls={calls['n']}")

    # ---- 5xx: retried but with ZERO back-off sleep, then raises fast ----
    calls["n"] = 0
    def post_503(*a, **k):
        calls["n"] += 1
        return FakeResp(503)
    c = client()
    with mock.patch.object(dhan_mod.requests, "post", post_503):
        t0 = _time.monotonic()
        try:
            c._post("/v2/charts/historical", {})
        except requests.HTTPError:
            pass
        el = _time.monotonic() - t0
    check("5xx retries without sleeping then raises",
          calls["n"] == c.max_retries and el < 1.0,
          f"calls={calls['n']}, {el:.2f}s")

    # ---- dead token + fresh local cache: cache still serves (no network) ----
    tmp = tempfile.mkdtemp()
    c = DhanClient("bad", cache_dir=tmp, min_interval=0.0)
    with open(os.path.join(tmp, "RELIANCE.csv"), "w", newline="") as f:
        f.write("date,open,high,low,close,volume\n")
        for i in range(1, 6):
            f.write(f"2026-08-0{i},100,105,99,102,1000\n")
    with mock.patch.object(
            dhan_mod.requests, "post",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!"))):
        bars = c.get_daily("RELIANCE", dt.date(2026, 8, 1),
                           dt.date(2026, 8, 5))
        check("fresh cache served without any network call",
              bars is not None and len(bars["close"]) == 5)
        # stale cache + dead token -> instant raise so yfinance takes over
        c._auth_dead = True
        try:
            c.get_daily("RELIANCE", dt.date(2026, 8, 1), dt.date(2026, 8, 7))
            err = None
        except DhanAuthError as e:
            err = e
        check("stale cache + dead token -> instant DhanAuthError",
              isinstance(err, DhanAuthError))


def test_run_live_all_scan_one_paths() -> None:
    """
    Regression (found in pre-merge review): scan_one returned 3-tuples on
    its error and prefilter-skip paths while run_live unpacked 4 values -
    the FIRST prefilter-skipped or data-failed symbol would crash the whole
    live scan with ValueError (the same bug class PR #2 fixed for 2->3).
    Exercise ALL THREE paths (error / prefilter-skip / signal) end-to-end.
    """
    print("== run_live: error + prefilter-skip + signal paths together ==")
    import os
    import tempfile
    import unittest.mock as mock
    import scanner as scanner_mod
    import dhan_client as dhan_mod
    from demo_data import demo_universe

    _dates, _bars, _exp = demo_universe()["SPORTKING"]
    _bars = {k: list(v) for k, v in _bars.items()}
    _bars["dates"] = list(_dates)

    n = 180
    red_bars = {
        "dates": [f"2026-{i//30+1:02d}-{i%28+1:02d}" for i in range(n)],
        "open": [100.0 + i * 0.1 for i in range(n)],
        "high": [103.0 + i * 0.1 for i in range(n)],
        "low": [97.0 + i * 0.1 for i in range(n)],
        "close": [98.0 + i * 0.1 for i in range(n)],  # close<open -> prefilter
        "volume": [1e6] * n,
    }

    class FakeClient:
        def __init__(self, *a, **k): pass
        def get_instruments(self):
            return {"GOOD": "1", "REDS": "2", "NODATA": "3"}
        def liquid_universe(self):
            return ["GOOD", "REDS", "NODATA"]
        def resolve_symbol(self, s): return "1"
        def get_daily(self, sym, *a, **k):
            if sym == "GOOD":
                return {kk: list(vv) for kk, vv in _bars.items()}
            if sym == "REDS":
                return {k2: list(v2) for k2, v2 in red_bars.items()}
            return None                     # NODATA: Dhan has nothing
        def intraday_partial(self, *a, **k): return None

    wl = os.path.join(tempfile.mkdtemp(), "watchlist.txt")
    with open(wl, "w") as f:
        f.write("GOOD\nREDS\nNODATA\n")

    cfg = ScanConfig()
    cfg.tracker_file = os.path.join(tempfile.mkdtemp(), "tracker.csv")

    import io
    import contextlib
    buf = io.StringIO()
    with mock.patch.object(dhan_mod, "DhanClient", FakeClient), \
         mock.patch.object(scanner_mod, "_yf_daily", lambda *a, **k: None):
        with contextlib.redirect_stdout(buf):
            rows = scanner_mod.run_live(
                cfg, "tok", "cid", limit=0, watchlist=wl, from_days=400,
                force_refresh=False, debug=False, intraday=False,
                notifier=None)
    out = buf.getvalue()
    check("all 3 paths survive one run (no unpack crash)", rows is not None)
    check("signal path still works", len(rows) == 1
          and rows[0]["symbol"] == "GOOD",
          f"rows={[(r['symbol'], r['score']) for r in rows]}")
    check("error path counted (NODATA)",
          "1 errors" in out and "NODATA" in out)
    check("prefilter path counted (REDS)",
          "1 prefilter-skipped" in out, out.splitlines()[-3:])


def test_backtest_finds_verified_stocks() -> None:
    """Regression: the backtest must find the 3 verified stocks on their
    exact signal dates (demo data), with ISO dates and recent flag."""
    print("== backtest finds the 3 verified stocks ==")
    import numpy as np
    import backtest as bt
    from config import ScanConfig
    from demo_data import demo_universe

    cfg = ScanConfig()
    rows = []
    expected = {"SPORTKING": "2026-07-31", "BAJFINANCE": "2026-07-27",
                "SPR_AUTO": "2026-07-27"}
    for sym, sig_date in expected.items():
        dates, bars, _ = demo_universe()[sym]
        bars2 = {k: np.array(v, float) for k, v in bars.items() if k != "symbol"}
        bars2["dates"] = list(dates)
        got = bt.backtest_symbol(sym, cfg, bars2, rows)
        check(f"{sym} found in backtest", got >= 1, f"got {got}")
        hit = [r for r in rows if r["symbol"] == sym and r["date"] == sig_date]
        check(f"{sym} on exact date {sig_date}", len(hit) == 1,
              f"dates found: {[r['date'] for r in rows if r['symbol']==sym]}")
        if hit:
            check(f"{sym} date is ISO (not epoch)", hit[0]["date"].count("-") == 2)
            check(f"{sym} has recent flag", "recent" in hit[0])


def test_tracker() -> None:
    print("== signal tracking sheet ==")
    import os, tempfile, datetime as dt
    import numpy as np
    from tracker import log_signal, read, update_open
    from pattern import detect_setup
    from demo_data import demo_universe

    dates, bars, _ = demo_universe()["BAJFINANCE"]
    sig = detect_setup(bars, dates, ScanConfig())
    f = os.path.join(tempfile.mkdtemp(), "tracker.csv")

    check("log adds row", log_signal(sig, f) is True)
    check("duplicate rejected", log_signal(sig, f) is False)
    rows = read(f)
    check("row has trade plan fields",
          rows and rows[0]["stop"] and rows[0]["target"] and rows[0]["rr"])

    class FakeClient:
        def get_daily(self, sym, frm, to, force_refresh=False):
            c = list(bars["close"]); dts = list(dates)
            last = c[-1]
            for k in range(10):
                last *= 1.015
                c.append(last)
                d = dt.date.fromisoformat(dts[-1]) + dt.timedelta(days=1)
                while d.weekday() >= 5:
                    d += dt.timedelta(days=1)
                dts.append(d.isoformat())
            return {"open": np.array(c), "high": np.array(c),
                    "low": np.array(c), "close": np.array(c),
                    "volume": np.ones(len(c)) * 1e6, "dates": dts}

    upd = update_open(f, FakeClient())
    rows = read(f)
    check("OPEN -> HIT after 5 sessions", upd == 1 and rows[0]["status"] == "HIT")
    check("r5 recorded", rows[0]["r5"] != "")


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
    test_symbol_in_alerts()
    test_universe()
    test_positives()
    test_aci_26w_proximity()
    test_backtest_finds_verified_stocks()
    test_tracker()
    test_run_live_unpack()
    test_cross_run_cooldown_and_summary()
    test_fail_fast_dhan_to_yfinance()
    test_run_live_all_scan_one_paths()
    test_negatives()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
