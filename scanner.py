#!/usr/bin/env python3
"""
BOS -> flush -> SSL-retest -> reversal scanner (daily, yfinance-backed).

Find stocks that just printed the shakeout-retest reversal candle BEFORE the
big momentum move - the exact setup seen in:

    SPORTKING  31-Jul-2026  (then +14% gap on 03-Aug)
    BAJFINANCE 27-Jul-2026  (then +8% by 31-Jul)
    SPR_AUTO   27-Jul-2026  (4523 line broken on 03-Aug)

It ALSO emits an EARLIER "SSL-ZONE TOUCH" alert the moment price dips INTO the
sell-side liquidity (SSL) level on the flush day (before the reversal candle
confirms) -- a separate, lighter early-warning message (see
`config.ssl_touch_alerts` / `pattern.detect_ssl_touch`).

Usage:
    # demo mode (built-in test data, no API needed):
    python scanner.py --mode demo

    # live scan of the whole NSE universe (yfinance-backed; no Dhan needed):
    export DHAN_ACCESS_TOKEN=your_token
    export DHAN_CLIENT_ID=your_client_id        # optional
    python scanner.py --mode live --limit 300

    # scan only your watchlist:
    python scanner.py --mode live --watchlist watchlist.txt

    # backtest the last 90 days of the demo universe:
    python scanner.py --mode demo --backtest --backtest-days 90
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys

import numpy as np

from config import ScanConfig
from env_loader import load_env
from pattern import detect_setup, detect_ssl_touch
from prefilter import load_mcap, mcap_filter, passes_prefilter
from telegram_notifier import TelegramNotifier
from tracker import log_signal, recently_alerted, update_open


TABLE_FIELDS = ["rank", "symbol", "score", "signal_date", "last_close",
                "bos_date", "bos_style", "break_level", "peak", "flush_low",
                "flush_date", "flush_drop%", "ssl", "min_close_after_ssl",
                "reversal_bounce%", "retrace%"]

TOUCH_TABLE_FIELDS = ["rank", "symbol", "signal_date", "last_close",
                      "bos_date", "bos_style", "peak", "flush_low",
                      "flush_date", "flush_drop%", "ssl"]

# Filled by run_live after every scan; main() reads it to decide the exit
# code (a data outage must turn the CI run RED, not green-and-silent).
LAST_RUN_STATS: dict = {}

# exit code when a live run was a data outage (distinct from 1=crash, 2=usage)
EXIT_DATA_OUTAGE = 3


def _table_rows(signals: list[dict]) -> list[dict]:
    out = []
    for i, s in enumerate(signals, 1):
        out.append({
            "rank": i,
            "symbol": s["symbol"],
            "score": s["score"],
            "signal_date": s["signal_date"],
            "last_close": round(s["last_close"], 2),
            "bos_date": s["bos_date"],
            "bos_style": s["bos_style"],
            "break_level": round(s["break_level"], 2),
            "peak": round(s["peak"], 2),
            "flush_low": round(s["flush_low"], 2),
            "flush_date": s["flush_date"],
            "flush_drop%": round(s["flush_drop_pct"], 1),
            "ssl": round(s["ssl"], 2),
            "min_close_after_ssl": round(s["min_close_after_ssl"], 2),
            "reversal_bounce%": round(s["bounce_pct"], 1),
            "retrace%": round(s["retrace_pct"], 0),
        })
    return out


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("\nNo signals today. The setup needs a BOS, a flush to the SSL "
              "zone that holds, and a fresh reversal candle - it is a rare pattern.")
        return
    hdr = ["#", "Symbol", "Score", "Signal", "Close", "BOS", "Style", "BrkLvl",
           "Peak", "FlushLow", "FlushD", "Drop%", "SSL", "MinCl>SSL", "Bounce%"]
    widths = [len(h) for h in hdr]
    data = []
    for r in rows:
        data.append([r["rank"], r["symbol"], f"{r['score']:.0f}", r["signal_date"],
                     f"{r['last_close']:.1f}", r["bos_date"], r["bos_style"],
                     f"{r['break_level']:.1f}", f"{r['peak']:.1f}",
                     f"{r['flush_low']:.1f}", r["flush_date"], f"{r['flush_drop%']:.1f}",
                     f"{r['ssl']:.1f}", f"{r['min_close_after_ssl']:.1f}",
                     f"{r['reversal_bounce%']:.1f}"])
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    print()
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(hdr)))
    print("  ".join("-" * widths[i] for i in range(len(hdr))))
    for row in data:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    print()


def _print_touches(touches: list[dict]) -> None:
    """Console summary of the SSL-zone TOUCH alerts (early-warning heads-up,
    separate from the reversal signal table)."""
    if not touches:
        return
    print()
    print("  SSL-ZONE TOUCHES (early warning — watch the level):")
    for t in sorted(touches, key=lambda s: -s["score"]):
        print(f"  · {t['symbol']:12s} {str(t['signal_date'])[:10]}  "
              f"low {t['flush_low']:.1f}  SSL {t['ssl']:.1f}  "
              f"flush −{t['flush_drop_pct']:.1f}%  "
              f"BOS {str(t['bos_date'])[:10]}")
    print()


def _write_touches_csv(touches: list[dict], path: str) -> None:
    """Write the SSL-zone TOUCH alerts to their own CSV (sidecar next to the
    signals CSV) so a run that fired touches leaves verifiable evidence."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TOUCH_TABLE_FIELDS)
        w.writeheader()
        for i, t in enumerate(touches, 1):
            w.writerow({
                "rank": i, "symbol": t["symbol"],
                "signal_date": t["signal_date"],
                "last_close": round(t["last_close"], 2),
                "bos_date": t["bos_date"], "bos_style": t["bos_style"],
                "peak": round(t["peak"], 2),
                "flush_low": round(t["flush_low"], 2),
                "flush_date": t["flush_date"],
                "flush_drop%": round(t["flush_drop_pct"], 1),
                "ssl": round(t["ssl"], 2),
            })
    print(f"wrote {len(touches)} SSL-touch rows -> {path}")


def _write_csv(rows: list[dict], path: str) -> None:
    # ALWAYS write the file, even with 0 signals: a quiet day used to
    # produce no output at all, which made the Actions upload step warn
    # "No files were found with the provided path: logs/" and left no
    # on-page evidence that the scan actually ran. A header-only CSV +
    # the .summary.txt sidecar (see main) make every run self-explanatory.
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TABLE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    if rows:
        print(f"wrote {len(rows)} rows -> {path}")
    else:
        print(f"no signals - wrote header-only CSV -> {path}")


def _write_summary(path: str, lines: list[str]) -> None:
    """Write the human-readable scan summary next to the signals CSV
    (<out>.summary.txt). Written on EVERY live run - including quiet ones -
    so the Actions artifact upload never warns 'No files were found:
    logs/' and a quiet day leaves verifiable evidence the scan ran."""
    try:
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _append_step_summary(lines: list[str]) -> None:
    """Append the same lines to GitHub's $GITHUB_STEP_SUMMARY when running
    inside Actions -> the stats show up on the run page even without
    opening the logs or waiting for the Telegram message."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n\n")
    except OSError:
        pass


def _apply_cooldowns(signals: list[dict], cooldown_days: int,
                     tracker_file: str, use_tracker: bool = True
                     ) -> tuple[list[dict], int, int]:
    """Apply the WITHIN-run dedup (same symbol re-firing within the cooldown
    window) and the CROSS-RUN tracker-backed cooldown (a symbol alerted on a
    previous run must not re-alert while the same bar is still served).

    Returns (kept, within_dropped, cross_dropped).

    Shared by the reversal PATTERN SIGNALS and the SSL-zone TOUCH alerts so
    both use identical cooldown semantics -- but with SEPARATE tracker files,
    so a touch does not suppress the later reversal signal (and vice versa).
    """
    within_dropped = 0
    if cooldown_days > 0 and signals:
        signals.sort(key=lambda s: (s["symbol"], str(s["signal_date"])[:10]))
        deduped, last_by_sym = [], {}
        for s in signals:
            sym = s["symbol"]
            date_s = str(s["signal_date"])[:10]
            if sym in last_by_sym:
                try:
                    import datetime as _dt
                    gap = (_dt.date.fromisoformat(date_s)
                           - _dt.date.fromisoformat(last_by_sym[sym])).days
                    if gap <= cooldown_days:
                        continue
                except ValueError:
                    pass
            last_by_sym[sym] = date_s
            deduped.append(s)
        within_dropped = len(signals) - len(deduped)
        signals = deduped

    cross_dropped = 0
    if cooldown_days > 0 and use_tracker and signals:
        kept = []
        for s in signals:
            try:
                if recently_alerted(s["symbol"], str(s["signal_date"])[:10],
                                    cooldown_days, tracker_file):
                    cross_dropped += 1
                    continue
            except Exception:  # noqa: BLE001  (bad tracker file -> alert anyway)
                pass
            kept.append(s)
        signals = kept
    return signals, within_dropped, cross_dropped


# --------------------------------------------------------------------------
# intraday (live market) helpers
# --------------------------------------------------------------------------

_YF_MISSING = False   # set once yfinance turns out to be uninstalled/DOA


def _yf_daily(sym: str, from_date, to_date):
    """Instant yfinance fallback for a symbol (Dhan -> Yahoo, no waiting)."""
    global _YF_MISSING
    try:
        import yfinance as yf
    except ImportError:
        _YF_MISSING = True      # permanent for this process: never retry
        return None
    try:
        yf_sym = {"SPR_AUTO": "SHRIPISTON"}.get(sym, sym)
        # NOTE: yfinance's `end` is EXCLUSIVE. Passing to_date directly
        # silently drops the last bar, so a fallback-only run scans the
        # market "as of yesterday" - the 16:30 IST EOD run used to work
        # off stale data whenever Dhan's 24h token had expired.
        end = to_date + dt.timedelta(days=1)
        df = yf.Ticker(f"{yf_sym}.NS").history(
            start=from_date, end=end, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty:
            return None
        return {
            "symbol": sym,
            "open": df["Open"].to_numpy(float),
            "high": df["High"].to_numpy(float),
            "low": df["Low"].to_numpy(float),
            "close": df["Close"].to_numpy(float),
            "volume": df["Volume"].to_numpy(float),
            "dates": [d.date().isoformat() for d in df.index],
        }
    except Exception:  # noqa: BLE001
        return None


def _yf_intraday_partial(sym: str, date):
    """yfinance fallback for today's partial candle (15m bars -> 1 daily bar)."""
    global _YF_MISSING
    try:
        import yfinance as yf
    except ImportError:
        _YF_MISSING = True
        return None
    try:
        yf_sym = {"SPR_AUTO": "SHRIPISTON"}.get(sym, sym)
        df = yf.Ticker(f"{yf_sym}.NS").history(
            period="1d", interval="15m", auto_adjust=True)
        if df is None or df.empty:
            return None
        return {
            "open": float(df["Open"].iloc[0]),
            "high": float(df["High"].max()),
            "low": float(df["Low"].min()),
            "close": float(df["Close"].iloc[-1]),
            "volume": float(df["Volume"].sum()),
        }
    except Exception:  # noqa: BLE001
        return None


class _YfGate:
    """
    Process-wide pacer for EVERY yfinance fallback call (all workers share
    one lock).

    Why this exists (the 2026-08-24 silent outage): when Dhan's 24h token
    is dead, every symbol falls back to Yahoo. Without pacing, ~800 symbols
    hit Yahoo in a free-running 3-worker burst; Yahoo 429-rate-limits the
    runner IP almost immediately, every fallback call then fails, and the
    scan "succeeds" in ~1 minute with ZERO data - 4 straight days of green
    runs with no possible alerts. A process-wide rate limit + ONE quick
    retry is what keeps the fallback alive; without it the fallback
    DDoSes itself exactly when Dhan is down.
    """

    def __init__(self, min_interval: float = 0.6, retry_delay: float = 1.2):
        import threading as _th
        self._lock = _th.Lock()
        self._last = 0.0
        self.min_interval = min_interval
        self.retry_delay = retry_delay

    def _pace(self) -> None:
        import time as _t
        with self._lock:
            wait = self.min_interval - (_t.monotonic() - self._last)
            if wait > 0:
                _t.sleep(wait)
            self._last = _t.monotonic()

    def call(self, fn, *args, **kwargs):
        """Paced call of a _yf_* helper. Skips call+retry entirely when
        yfinance is not importable (permanent for this process); otherwise
        one retry after `retry_delay` for transient Yahoo 429s."""
        if _YF_MISSING:
            return None
        self._pace()
        result = fn(*args, **kwargs)
        if result is None and not _YF_MISSING and self.retry_delay > 0:
            import time as _t
            _t.sleep(self.retry_delay)
            self._pace()
            result = fn(*args, **kwargs)
        return result


def _iso_date(ts) -> str:
    """Normalize any bar timestamp (epoch s/ms, datetime, or ISO string)
    to YYYY-MM-DD. Local copy so live mode stays Dhan-free (the yfinance
    fetchers already return ISO strings; this is belt-and-braces for any
    future source). Same semantics as dhan_client._iso_date."""
    s = str(ts)
    if s.isdigit():
        v = int(s)
        if v > 1e12:
            v //= 1000          # milliseconds -> seconds
        if v > 1e10:
            v //= 1000
        try:
            return (dt.datetime.fromtimestamp(v, dt.timezone.utc)
                    .strftime("%Y-%m-%d"))
        except (ValueError, OSError, OverflowError):
            return s[:10]
    return s[:10]


class _TrackerYfClient:
    """update_open() expects a client with get_daily(sym, from, to).
    Post-#15 the live scan is Dhan-free, so back the tracker with the
    same paced yfinance fetch instead of the old Dhan client."""

    def __init__(self, gate: "_YfGate"):
        self._gate = gate

    def get_daily(self, sym, from_date, to_date):
        return self._gate.call(_yf_daily, sym, from_date, to_date)


def merge_partial(bars: dict, partial: dict, today: str):
    """
    Append a partial (intraday-so-far) candle to a daily bars dict.
    Returns (merged_bars, merged_bool).
    If the daily series already contains `today`, nothing is merged and
    merged_bool is False (the daily bar is already the live partial bar).
    The merged bars get 'partial_last': True so the detector knows the
    last candle is still forming (e.g. for the volume filter).
    """
    if bars.get("dates") and bars["dates"][-1] >= today:
        return bars, False
    # bars may come from Dhan (python lists) OR the yfinance fallback
    # (numpy arrays) - coerce every sequence to a list and KEEP scalar
    # keys like 'symbol' (dropping it made live alerts show "?" again).
    ohlcv = ("dates", "open", "high", "low", "close", "volume")
    if any(k not in bars for k in ohlcv):
        return bars, False          # malformed input -> merge nothing
    b = {}
    for k, v in bars.items():
        if isinstance(v, (list, tuple)) or isinstance(v, np.ndarray):
            b[k] = list(v)
        else:
            b[k] = v                # 'symbol', flags, etc.
    b["dates"].append(today)
    b["open"].append(partial["open"])
    b["high"].append(partial["high"])
    b["low"].append(partial["low"])
    b["close"].append(partial["close"])
    b["volume"].append(partial["volume"])
    b["partial_last"] = True
    return b, True


# --------------------------------------------------------------------------
# demo mode
# --------------------------------------------------------------------------

def run_demo(cfg: ScanConfig, backtest: bool, backtest_days: int,
             notifier: TelegramNotifier | None = None) -> list[dict]:
    from demo_data import demo_universe
    universe = demo_universe()

    if backtest:
        hits = []
        for sym, (dates, bars, _exp) in universe.items():
            n = len(bars["close"])
            start = max(cfg.min_bars, n - backtest_days)
            for t in range(start, n):
                sl = {k: v[: t + 1] for k, v in bars.items()}
                sig = detect_setup(sl, dates[: t + 1], cfg)
                if sig:
                    hits.append(sig)
        hits.sort(key=lambda s: (s["signal_date"], -s["score"]))
        print(f"Backtest {backtest_days}d over {len(universe)} demo symbols: "
              f"{len(hits)} signals")
        rows = _table_rows(hits)
        _print_table(rows)
        return rows

    signals = []
    touches = []
    for sym, (dates, bars, expected) in universe.items():
        sig = detect_setup(bars, dates, cfg)
        if sig and sig.get("score", 0) < cfg.score_threshold:
            sig = None
        mark = ""
        if sig:
            ok = (expected is not None and sig["signal_date"] == expected)
            mark = "  <- EXPECTED FLAG " if ok else "  <- (unexpected)"
            signals.append(sig)
        elif expected is not None:
            mark = "  !! MISSED (should have flagged)"
        if sig or expected is not None:
            print(f"{sym:16s} expected={expected} got={sig['signal_date'] if sig else None}{mark}")
        # ---- SSL-zone touch (early-warning) ----  # noqa: E501
        if cfg.ssl_touch_alerts:
            touch = detect_ssl_touch(bars, dates, cfg)
            if touch:
                # if the same bar is already a full reversal signal, the touch
                # would be redundant (HEG-style: touch == reversal day)
                if sig is not None and str(touch["signal_date"])[:10] \
                        == str(sig["signal_date"])[:10]:
                    touch = None
                if touch:
                    touches.append(touch)
    rows = _table_rows(sorted(signals, key=lambda s: -s["score"]))
    _print_table(rows)
    _print_touches(touches)

    if notifier is not None:
        scope = f"demo ({len(universe)} symbols)"
        sent = notifier.send_signals(signals, scope)
        if touches:
            sent += notifier.send_ssl_touches(touches, scope)
        print(f"Telegram: {sent} messages sent ({scope})")

    return rows


# --------------------------------------------------------------------------
# live mode
# --------------------------------------------------------------------------

def run_live(cfg: ScanConfig, token: str, client_id: str | None, limit: int,
             watchlist: str | None, from_days: int, force_refresh: bool,
             debug: bool, intraday: bool = False,
             notifier: TelegramNotifier | None = None,
             summary_sink: list[str] | None = None,
             out_path: str | None = None) -> list[dict]:
    from universes import get_universe

    def _summary(line: str) -> None:
        # every line the user should see on the Actions run page (and as
        # proof a quiet day was a HEALTHY quiet day, not a silent failure)
        if summary_sink is not None:
            summary_sink.append(line)

    # ---- universe: watchlist or market_cap.csv fallback (no Dhan) ----
    if watchlist:
        with open(watchlist) as f:
            symbols = [ln.strip().upper() for ln in f if ln.strip()
                       and not ln.strip().startswith("#")]
        source = f"watchlist ({len(symbols)} syms)"
        print(f"universe: {source}")
    else:
        # read symbols from the bundled market_cap.csv (symbol, mcap_cr)
        mcap_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "market_cap.csv")
        if not os.path.exists(mcap_path):
            mcap_path = os.path.join(os.getcwd(), "data", "market_cap.csv")
        mcap = load_mcap(mcap_path)
        if mcap:
            symbols = list(mcap.keys())
            print(f"universe: market_cap.csv ({len(symbols)} syms)")
        else:
            # fallback to the built-in demo universe (small set)
            from demo_data import demo_universe
            symbols = list(demo_universe().keys())
            print(f"universe: demo universe ({len(symbols)} syms)")
    if limit:
        symbols = symbols[:limit]
        print(f"limit: scanning first {limit} symbols")
    # ---- prefilter: market cap > X Cr BEFORE any API calls (big win) ----
    pref_skipped = 0
    mcap = None                    # (else NameError below when prefilter off)
    if cfg.prefilter_enabled:
        mcap = load_mcap(cfg.mcap_file)
        if mcap:
            before = len(symbols)
            symbols, dropped = mcap_filter(symbols, mcap, cfg.prefilter_mcap_min)
            print(f"mcap prefilter: {before} -> {len(symbols)} "
                  f"(dropped {dropped} below {cfg.prefilter_mcap_min:.0f} Cr)")
        else:
            print(f"WARNING: {cfg.mcap_file} not found - market cap filter "
                  f"skipped (run build_mcap.py once)")

    # ---- hard cap so a run finishes in ~5-8 min (not 60+) ----
    if len(symbols) > cfg.max_symbols_scan:
        if mcap:
            # keep the highest-mcap symbols (best candidates first)
            symbols = sorted(
                symbols, key=lambda s: mcap.get(s, 0.0), reverse=True
            )[:cfg.max_symbols_scan]
        else:
            symbols = symbols[:cfg.max_symbols_scan]
        print(f"universe capped: scanning {len(symbols)} symbols "
              f"(max_symbols_scan={cfg.max_symbols_scan})")

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=from_days)
    today_s = to_date.isoformat()

    # ---------------------------------------------------------------- scan
    import time as _t
    t0 = _t.time()
    signals, touches, errors = [], [], 0
    total = len(symbols)
    # the yfinance fallback is PACED process-wide: paced to avoid Yahoo 429s
    yf_gate = _YfGate(cfg.yf_min_interval, cfg.yf_retry_delay)
    import threading as _thr
    _pref_lock = _thr.Lock()
    _pref_reasons: dict[str, int] = {}

    def _pref_bucket(why: str) -> str:
        for token in ("weekly RSI", "weekly MACD", "daily candle",
                      "close", "insufficient"):
            if token in why:
                return token if token != "close" else "close < min"
        return why[:24] or "other"

    def scan_one(sym: str):
        """Fetch + detect for one symbol. Returns a 5-tuple
        (sig, touch, err, pref_skipped, near_miss) - EVERY path must return
        all five or run_live's unpack crashes the whole scan.
        PRIMARY = yfinance (paced); on any failure the paced yfinance fallback
        is used exclusively - no Dhan dependency.
        """
        # ---- 1) yfinance daily bars (paced) ----
        bars = yf_gate.call(_yf_daily, sym, from_date, to_date)
        if bars is None:
            # A failed fetch MUST be counted as an error: the outage
            # detector compares errors/total, and the 2026-08-28 lesson is
            # that a blind scanner must never look like a healthy quiet
            # day (0 errors, "no signals today").
            return None, None, "NODATA: no bars from yfinance", False, None

        # ---- normalize dates to ISO (epoch-safe, cache-safe) ----
        bars["dates"] = [_iso_date(d) for d in bars["dates"]]
        # the symbol is dropped by some sources (cache reads) - without it
        # the Telegram alert arrives as "PATTERN SIGNAL — ?" and you can't
        # tell which stock it is
        bars["symbol"] = sym

        # ---- prefilter: weekly RSI/MACD + close>100 + green daily ----
        #   This gates the REVERSAL PATTERN SIGNAL only. The SSL-zone TOUCH is an
        #   EARLY-WARNING alert on the flush day: the candle is naturally RED and
        #   a deep flush can drag weekly RSI/MACD down (e.g. JSFB's -14.5% flush),
        #   so the touch BYPASSES the panel and relies entirely on the structural
        #   detector's BOS / flush / SSL / price / volume gates (detect_ssl_touch).
        pref_blocked = False
        if cfg.prefilter_enabled:
            ok, why = passes_prefilter(bars, cfg)
            if not ok:
                # tally WHY candidates are dropped -> a quiet day must be
                # explainable ("638 prefilter-skipped: weekly RSI 512,
                # green daily 97, ...") instead of a black box
                if isinstance(why, str):
                    with _pref_lock:
                        key = _pref_bucket(why)
                        _pref_reasons[key] = _pref_reasons.get(key, 0) + 1
                pref_blocked = True   # removed from the REVERSAL scan

        live_merged = False
        if intraday:
            last_d = bars["dates"][-1] if bars["dates"] else ""
            if last_d < today_s:
                partial = yf_gate.call(_yf_intraday_partial, sym, to_date)
                if partial:
                    try:
                        bars, live_merged = merge_partial(bars, partial,
                                                          today_s)
                    except Exception:  # noqa: BLE001
                        # a bad partial for ONE symbol must never kill the
                        # whole scan - fall back to completed daily bars
                        live_merged = False

        # ---- reversal detection (needs the full panel to pass) ----
        sig = None
        near = None
        if not pref_blocked:
            sig = detect_setup(bars, bars["dates"], cfg)
            if sig and sig.get("score", 0) < cfg.score_threshold:
                # below the alert threshold -> not a signal, but remember the
                # near-misses (score within 15 pts) so the daily summary can
                # explain WHY a quiet day is quiet ("2 setups scored 62-69")
                if sig["score"] >= cfg.score_threshold - 15:
                    near = (sym, sig["score"], str(sig["signal_date"])[:10])
                sig = None
        if sig:
            sig["intraday"] = bool(live_merged)

        # ---- SSL-zone TOUCH (early-warning add-on; runs even on a red flush
        #      day, independent of the reversal signal) ----
        touch = detect_ssl_touch(bars, bars["dates"], cfg) if cfg.ssl_touch_alerts \
            else None
        if touch and sig and str(touch["signal_date"])[:10] \
                == str(sig["signal_date"])[:10]:
            # the same bar is already a confirmed reversal (HEG-style touch ==
            # reversal day): don't double-alert, the reversal message covers it
            touch = None
        if touch:
            touch["intraday"] = bool(live_merged)
        return sig, touch, "", pref_blocked, near

    from concurrent.futures import ThreadPoolExecutor, as_completed
    err_by_type: dict[str, int] = {}
    err_samples: list[str] = []
    near_misses: list[tuple] = []
    with ThreadPoolExecutor(max_workers=cfg.live_max_workers) as pool:
        futs = {pool.submit(scan_one, s): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            done += 1
            sym = futs[fut]
            sig, touch, err, pref, near = fut.result()
            if err:
                errors += 1
                key = err.split(":")[0].split("for url")[0].strip()[:60]
                err_by_type[key] = err_by_type.get(key, 0) + 1
                if len(err_samples) < 5:
                    err_samples.append(f"[{sym}] {err[:150]}")
                if debug:
                    print(f"  [{sym}] ERROR {err[:120]}")
            elif sig:
                signals.append(sig)
                tag = "LIVE " if sig.get("intraday") else ""
                print(f"  SIGNAL {tag}{sym:12s} score={sig['score']:.0f} "
                      f"signal={sig['signal_date']} ssl={sig['ssl']:.1f} "
                      f"flush={sig['flush_date']}")
            elif touch:
                touches.append(touch)
                tag = "LIVE " if touch.get("intraday") else ""
                print(f"  TOUCH  {tag}{sym:12s} low={touch['flush_low']:.1f} "
                      f"ssl={touch['ssl']:.1f} "
                      f"date={touch['signal_date']}")
            elif pref:
                pref_skipped += 1
            if near is not None:
                near_misses.append(near)
            if done % 100 == 0 or done == total:
                el = _t.time() - t0
                rate = done / max(el, 1e-6)
                eta = (total - done) / rate if rate > 0 else float("inf")
                print(f"  ... {done}/{total} scanned "
                      f"({len(signals)} signals, {len(touches)} touches, "
                      f"{errors} errors, "
                      f"{pref_skipped} prefilter-skipped) "
                      f"[{el:.0f}s elapsed, {rate:.1f} sym/s, "
                      f"ETA {eta/60:.1f} min]", flush=True)
    import time as _t
    print(f"scanned {total} symbols, {len(signals)} signals, "
          f"{len(touches)} touches, {errors} errors, "
          f"{pref_skipped} prefilter-skipped in {_t.time() - t0:.0f}s")
    _summary(f"scan: {total} scanned · {len(signals)} signals · "
             f"{len(touches)} touches · {errors} errors · "
             f"{pref_skipped} prefilter-skipped "
             f"· score>= {cfg.score_threshold:.0f}")
    # ---- data-source health: yfinance fallback is FINE for daily bars ----
    # (a silently-stale token is no longer an issue since we no longer use Dhan)
    _summary("data: yfinance fallback OK")

    # ---- DATA OUTAGE: most symbols fetched NOTHING (yfinance fallback failing)
    #      yfinance fallback failing). '0 signals' in this state is a lie -
    #      the scanner was blind. For 4 days around 2026-08-24 every run
    #      was an outage that looked like a polite quiet day (green check,
    #      'no pattern signals today'), which is exactly the confusion that
    #      hid the outage. Make it LOUD: console error annotation, a
    #      distinct summary line, a dedicated Telegram message, and a
    #      non-zero exit code (main) so the Actions run turns red. ----
    outage = (total >= cfg.data_outage_min_symbols
              and errors >= cfg.data_outage_error_frac * total)
    outage_text = ""
    if outage:
        pct = 100.0 * errors / max(total, 1)
        outage_text = (
            f"{errors}/{total} symbols failed to fetch ({pct:.0f}%)"
            + (" · yfinance fallback also failing (Yahoo rate-limit?)"
               if errors else ""))
        print(f"::error title=Scanner data outage::{outage_text}. This run "
              f"had NO usable market data - treat '0 signals' as invalid.")
        print(f"WARNING: DATA OUTAGE - {outage_text}", file=sys.stderr)
        _summary(f"🛑 data: OUTAGE - {outage_text} - alerts impossible")
    else:
        _summary(f"data health: {total - errors}/{total} fetched "
                 f"({errors} failed)")
    if _pref_reasons:
        top = sorted(_pref_reasons.items(), key=lambda kv: -kv[1])[:3]
        _summary("prefilter rejects: "
                 + " · ".join(f"{k} {v}" for k, v in top))
    LAST_RUN_STATS.clear()
    LAST_RUN_STATS.update({
        "total": total, "errors": errors, "signals": len(signals),
        "touches": len(touches), "pref_skipped": pref_skipped,
        "outage": outage,
    })
    if errors and err_by_type:
        print("error breakdown:")
        for k, v in sorted(err_by_type.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {v:4d} x {k}")
        for s in err_samples:
            print(f"  e.g. {s}")

    # ---- cooldown: drop repeat signals for the same symbol within
    #      cooldown_days (backtest: repeats win only 30% vs 63% first). The
    #      cross-run tracker-backed cooldown prevents the SAME setup re-alerting
    #      on every run that still serves the same last bar (2026-08-19 saw 5
    #      identical re-alerts of the 08-18 signal). Shared helper => identical
    #      semantics for signals and touches, but SEPARATE tracker files so a
    #      touch never suppresses the later reversal signal (and vice versa). ---
    signals, dropped, cooldown_skipped = _apply_cooldowns(
        signals, cfg.cooldown_days, cfg.tracker_file, cfg.tracker_enabled)
    if dropped:
        print(f"cooldown: dropped {dropped} repeat signals within "
              f"{cfg.cooldown_days}d")
    if cooldown_skipped:
        print(f"cross-run cooldown: suppressed {cooldown_skipped} "
              f"re-alert(s) (already alerted within {cfg.cooldown_days}d "
              f"per {cfg.tracker_file})")

    # ---- SSL-zone TOUCHES: their own cooldown/tracker (separate type) ----
    touch_dropped = 0
    touch_cooldown = 0
    if cfg.ssl_touch_alerts and touches:
        touches, touch_dropped, touch_cooldown = _apply_cooldowns(
            touches, cfg.ssl_touch_cooldown_days,
            cfg.ssl_touch_tracker_file, cfg.tracker_enabled)
        if touch_dropped:
            print(f"cooldown: dropped {touch_dropped} repeat touch(es) within "
                  f"{cfg.ssl_touch_cooldown_days}d")
        if touch_cooldown:
            print(f"cross-run cooldown: suppressed {touch_cooldown} "
                  f"re-alert touch(es) (already alerted within "
                  f"{cfg.ssl_touch_cooldown_days}d per "
                  f"{cfg.ssl_touch_tracker_file})")
    # reflect the ACTUAL (post-cooldown) touch count in the run stats so the
    # summary line and the Telegram stats don't overstate suppressed touches
    LAST_RUN_STATS["touches"] = len(touches) if cfg.ssl_touch_alerts else 0

    if near_misses:
        near_misses.sort(key=lambda x: -x[1])
        print(f"near-misses (pattern OK, score < {cfg.score_threshold:.0f}): "
              + ", ".join(f"{s}:{sc:.0f}" for s, sc, _ in near_misses[:10]))
        _summary("near-misses: " + ", ".join(f"{s} {sc:.0f}"
                                             for s, sc, _ in near_misses[:10]))
    if cooldown_skipped:
        _summary(f"cooldown: {cooldown_skipped} re-alert(s) suppressed")
    if touch_cooldown:
        _summary(f"cooldown: {touch_cooldown} re-alert touch(es) suppressed")

    rows = _table_rows(sorted(signals, key=lambda s: -s["score"]))
    _print_table(rows)
    _print_touches(touches)

    # ---- tracking sheet: log new signals + mark OPEN rows HIT/MISS ----
    if cfg.tracker_enabled:
        added = sum(1 for s in signals if log_signal(s, cfg.tracker_file))
        try:
            updated = update_open(cfg.tracker_file, _TrackerYfClient(yf_gate))
        except Exception as e:  # noqa: BLE001
            updated = 0
            print(f"WARNING: tracker update failed: {e}", file=sys.stderr)
        print(f"tracker: {added} new logged, {updated} OPEN -> HIT/MISS "
              f"({cfg.tracker_file})")
        # touches land in their own tracker file (separate cooldown type) so
        # they never suppress / pollute the reversal tracker
        if cfg.ssl_touch_alerts and touches:
            t_added = sum(1 for t in touches
                          if log_signal(t, cfg.ssl_touch_tracker_file))
            print(f"tracker: {t_added} new SSL-touch logged "
                  f"({cfg.ssl_touch_tracker_file})")
            # a touches CSV sidecar so a run that fired touches leaves
            # verifiable evidence next to the signals CSV
            if out_path:
                try:
                    _write_touches_csv(touches, out_path + ".touches.csv")
                except OSError as e:  # noqa: BLE001
                    print(f"WARNING: touches CSV write failed: {e}",
                          file=sys.stderr)

    if notifier is not None:
        scope = f"{len(symbols)} symbols"
        stats = (f"scanned {len(symbols)} · prefilter-skipped {pref_skipped} "
                 f"· errors {errors}")
        if cooldown_skipped:
            stats += f" · {cooldown_skipped} re-alert(s) suppressed by cooldown"
        if touch_cooldown:
            stats += (f" · {touch_cooldown} re-alert touch(es) suppressed "
                      f"by cooldown")
        if near_misses:
            stats += (" · near-misses (score < "
                      f"{cfg.score_threshold:.0f}): "
                      + ", ".join(f"{s} {sc:.0f}" for s, sc, _ in near_misses[:5]))
        if outage:
            # LOUD outage message FIRST (the polite "no signals today"
            # summary below would otherwise whitewash a blind scan)
            sent = 1 if notifier.send_data_outage(outage_text) else 0
            sent += notifier.send_signals(signals, scope, stats)
        else:
            sent = notifier.send_signals(signals, scope, stats)
        if cfg.ssl_touch_alerts and touches:
            sent += notifier.send_ssl_touches(touches, scope, stats)
        print(f"Telegram: {sent} messages sent ({scope})")

    return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["demo", "live"], default="demo",
                   help="demo = built-in test data; live = Dhan API")
    p.add_argument("--token", default=None, help="Dhan access token (optional, yfinance used if absent)"
                   "(or set DHAN_ACCESS_TOKEN)")
    p.add_argument("--client-id", default=None, help="Dhan client id (optional, yfinance used if absent)"
                   "(or set DHAN_CLIENT_ID)")
    p.add_argument("--limit", type=int, default=0,
                   help="live: scan only the first N symbols")
    p.add_argument("--watchlist", default=None,
                   help="live: file with one symbol per line")
    p.add_argument("--out", default=None, help="write results to CSV")
    p.add_argument("--threshold", type=float, default=None,
                   help="min score to report (default from config: 70)")
    p.add_argument("--backtest", action="store_true",
                   help="walk history and list all signals in the window")
    p.add_argument("--backtest-days", type=int, default=90)
    p.add_argument("--from-days", type=int, default=400,
                   help="live: how many calendar days of history to fetch")
    p.add_argument("--refresh", action="store_true",
                   help="live: ignore cached daily bars")
    p.add_argument("--intraday", action="store_true",
                   help="live: also fetch today's partial candle (yfinance)"
                        "intraday data so signals fire DURING market hours "
                        "(run this every ~15 min while the market is open)")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--telegram", action="store_true",
                   help="send alerts to Telegram (needs TELEGRAM_BOT_TOKEN "
                        "and TELEGRAM_CHAT_ID in .env or environment)")
    p.add_argument("--env-file", default=".env",
                   help="path to the .env file (default: ./.env)")
    args = p.parse_args(argv)

    # load .env BEFORE reading tokens (does not override existing env vars)
    load_env(args.env_file)

    cfg = ScanConfig()
    if args.threshold is not None:
        cfg.score_threshold = args.threshold

    notifier = None
    if args.telegram:
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        if tg_token and tg_chat:
            notifier = TelegramNotifier(tg_token, tg_chat)
        else:
            print("WARNING: --telegram given but TELEGRAM_BOT_TOKEN / "
                  "TELEGRAM_CHAT_ID not set — continuing without alerts.",
                  file=sys.stderr)

    if args.mode == "demo":
        rows = run_demo(cfg, args.backtest, args.backtest_days, notifier)
    else:
        token = args.token or os.environ.get("DHAN_ACCESS_TOKEN")
        client_id = args.client_id or os.environ.get("DHAN_CLIENT_ID")
        if not token:
            print("Dhan access token not needed (yfinance used by default)"
                  "or pass --token (create one at Dhan -> Settings -> API).",
                  file=sys.stderr)
            return 2
        summary: list[str] = []
        rows = run_live(cfg, token, client_id, args.limit, args.watchlist,
                        args.from_days, args.refresh, args.debug,
                        args.intraday, notifier, summary_sink=summary,
                        out_path=args.out)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip()
        outage = bool(LAST_RUN_STATS.get("outage"))
        touches_n = LAST_RUN_STATS.get("touches", 0)
        result = (f"result: 🛑 DATA OUTAGE - {LAST_RUN_STATS.get('errors', 0)}/"
                  f"{LAST_RUN_STATS.get('total', 0)} fetches failed; "
                  f"{len(rows)} signal(s) INVALID") if outage else \
                 (f"result: {len(rows)} signal(s)"
                  + (f", {touches_n} SSL-touch(es)" if touches_n else ""))
        summary = [f"shakeout scan - {stamp} "
                   f"({'intraday' if args.intraday else 'eod'})"] + summary \
                  + [result]
        _append_step_summary(summary)
        if args.out:
            _write_summary(args.out + ".summary.txt", summary)
            if rows:
                _write_csv(rows, args.out)
        # a data outage is a FAILED scan, not a quiet day: non-zero exit
        # turns the scheduled Actions run red so it gets investigated
        return EXIT_DATA_OUTAGE if outage else 0

    if args.out and rows:
        _write_csv(rows, args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
