#!/usr/bin/env python3
"""
BOS -> flush -> SSL-retest -> reversal scanner (daily, NSE via Dhan API).

Find stocks that just printed the shakeout-retest reversal candle BEFORE the
big momentum move - the exact setup seen in:

    SPORTKING  31-Jul-2026  (then +14% gap on 03-Aug)
    BAJFINANCE 27-Jul-2026  (then +8% by 31-Jul)
    SPR_AUTO   27-Jul-2026  (4523 line broken on 03-Aug)

Usage:
    # demo mode (built-in test data, no API needed):
    python scanner.py --mode demo

    # live scan of the whole NSE universe (needs Dhan token):
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
from pattern import detect_setup
from prefilter import load_mcap, mcap_filter, passes_prefilter
from telegram_notifier import TelegramNotifier
from tracker import log_signal, recently_alerted, update_open


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


def _write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")


# --------------------------------------------------------------------------
# intraday (live market) helpers
# --------------------------------------------------------------------------

def _yf_daily(sym: str, from_date, to_date):
    """Instant yfinance fallback for a symbol (Dhan -> Yahoo, no waiting)."""
    try:
        import yfinance as yf
        yf_sym = {"SPR_AUTO": "SHRIPISTON"}.get(sym, sym)
        df = yf.Ticker(f"{yf_sym}.NS").history(
            start=from_date, end=to_date, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
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
    try:
        import yfinance as yf
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
    rows = _table_rows(sorted(signals, key=lambda s: -s["score"]))
    _print_table(rows)

    if notifier is not None:
        scope = f"demo ({len(universe)} symbols)"
        sent = notifier.send_signals(signals, scope)
        print(f"Telegram: {sent} messages sent ({scope})")

    return rows


# --------------------------------------------------------------------------
# live mode
# --------------------------------------------------------------------------

def run_live(cfg: ScanConfig, token: str, client_id: str | None, limit: int,
             watchlist: str | None, from_days: int, force_refresh: bool,
             debug: bool, intraday: bool = False,
             notifier: TelegramNotifier | None = None) -> list[dict]:
    from dhan_client import DhanClient, DhanAuthError
    from universes import get_universe

    client = DhanClient(token, client_id,
                        min_interval=cfg.live_request_interval,
                        timeout=cfg.api_timeout)

    # ---- universe: Dhan instrument map first, watchlist/fallback otherwise ----
    try:
        instruments = client.get_instruments()
        print(f"instrument map: {len(instruments)} NSE equities")
    except Exception as e:  # noqa: BLE001
        instruments = {}
        print(f"WARNING: instrument map unavailable ({e}); "
              f"falling back to watchlist/static list", file=sys.stderr)

    if watchlist:
        with open(watchlist) as f:
            symbols = [ln.strip().upper() for ln in f if ln.strip()
                       and not ln.strip().startswith("#")]
        source = f"watchlist ({len(symbols)} syms)"
        print(f"universe: {source}")
    elif instruments:
        symbols = client.liquid_universe() or sorted(instruments.keys())
        source = f"dhan-instruments ({len(symbols)} syms)"
        print(f"universe: {source}")
    else:
        symbols, source = get_universe()
        print(f"universe: {source}")
        # only keep symbols we can resolve (need a security id)
        if not instruments:
            resolved = [s for s in symbols if client.resolve_symbol(s)]
            print(f"  -> {len(resolved)} symbols resolvable via instrument map")
            symbols = resolved
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
    signals, errors = [], 0
    total = len(symbols)
    _auth_warned = [False]   # one-time "Dhan token dead" console warning

    def scan_one(sym: str):
        """Fetch + detect for one symbol. Returns (sig, err, pref_skipped).
        PRIMARY = Dhan; on ANY failure, INSTANT yfinance fallback (no wait)."""
        # ---- 1) Dhan ----
        bars = None
        dhan_err = ""
        try:
            bars = client.get_daily(sym, from_date, to_date,
                                    force_refresh=force_refresh)
        except DhanAuthError as e:
            # dead/expired token: the client now fails every call instantly,
            # so the whole run finishes on the yfinance fallback at full speed
            dhan_err = f"DhanAuthError: {e}"[:80]
            if not _auth_warned[0]:
                _auth_warned[0] = True
                print("WARNING: Dhan rejected the access token (401/403) - "
                      "scanning continues on the yfinance fallback only. "
                      "Rotate DHAN_ACCESS_TOKEN to restore Dhan data.",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            dhan_err = str(e)[:60]
        if bars is None or len(bars.get("close", [])) < cfg.min_bars:
            # ---- 2) instant yfinance fallback ----
            bars = _yf_daily(sym, from_date, to_date)
            if bars is None:
                return None, dhan_err or "no data (dhan+yf failed)", False

        # ---- normalize dates to ISO (epoch-safe, cache-safe) ----
        from dhan_client import _iso_date
        bars["dates"] = [_iso_date(d) for d in bars["dates"]]
        # the symbol is dropped by some sources (Dhan cache reads, the
        # yfinance fallback) - without it the Telegram alert arrives as
        # "PATTERN SIGNAL — ?" and you can't tell which stock it is
        bars["symbol"] = sym

        # ---- prefilter: weekly RSI/MACD + close>100 + green daily ----
        if cfg.prefilter_enabled:
            ok, why = passes_prefilter(bars, cfg)
            if not ok:
                return None, "", True

        live_merged = False
        if intraday:
            last_d = bars["dates"][-1] if bars["dates"] else ""
            if last_d < today_s:
                partial = None
                try:
                    partial = client.intraday_partial(sym, to_date)
                except Exception:  # noqa: BLE001
                    partial = None
                if partial is None:
                    partial = _yf_intraday_partial(sym, to_date)  # fallback
                if partial:
                    try:
                        bars, live_merged = merge_partial(bars, partial,
                                                          today_s)
                    except Exception:  # noqa: BLE001
                        # a bad partial for ONE symbol must never kill the
                        # whole scan - fall back to completed daily bars
                        live_merged = False

        sig = detect_setup(bars, bars["dates"], cfg)
        near = None
        if sig and sig.get("score", 0) < cfg.score_threshold:
            # below the alert threshold -> not a signal, but remember the
            # near-misses (score within 15 pts) so the daily summary can
            # explain WHY a quiet day is quiet ("2 setups scored 62-69")
            if sig["score"] >= cfg.score_threshold - 15:
                near = (sym, sig["score"], str(sig["signal_date"])[:10])
            sig = None
        if sig:
            sig["intraday"] = bool(live_merged)
        return sig, "", False, near

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
            sig, err, pref, near = fut.result()
            if err:
                errors += 1
                key = err.split(":")[0].split("for url")[0].strip()[:60]
                err_by_type[key] = err_by_type.get(key, 0) + 1
                if len(err_samples) < 5:
                    err_samples.append(f"[{sym}] {err[:150]}")
                if debug:
                    print(f"  [{sym}] ERROR {err[:120]}")
            elif pref:
                pref_skipped += 1
            elif sig:
                signals.append(sig)
                tag = "LIVE " if sig.get("intraday") else ""
                print(f"  SIGNAL {tag}{sym:12s} score={sig['score']:.0f} "
                      f"signal={sig['signal_date']} ssl={sig['ssl']:.1f} "
                      f"flush={sig['flush_date']}")
            if near is not None:
                near_misses.append(near)
            if done % 100 == 0 or done == total:
                el = _t.time() - t0
                rate = done / max(el, 1e-6)
                eta = (total - done) / rate if rate > 0 else float("inf")
                print(f"  ... {done}/{total} scanned "
                      f"({len(signals)} signals, {errors} errors, "
                      f"{pref_skipped} prefilter-skipped) "
                      f"[{el:.0f}s elapsed, {rate:.1f} sym/s, "
                      f"ETA {eta/60:.1f} min]", flush=True)
    import time as _t
    print(f"scanned {total} symbols, {len(signals)} signals, {errors} errors, "
          f"{pref_skipped} prefilter-skipped in {_t.time() - t0:.0f}s")
    if errors and err_by_type:
        print("error breakdown:")
        for k, v in sorted(err_by_type.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {v:4d} x {k}")
        for s in err_samples:
            print(f"  e.g. {s}")

    # ---- cooldown: drop repeat signals for the same symbol within
    #      cooldown_days (backtest: repeats win only 30% vs 63% first) ----
    if cfg.cooldown_days > 0 and signals:
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
                    if gap <= cfg.cooldown_days:
                        continue
                except ValueError:
                    pass
            last_by_sym[sym] = date_s
            deduped.append(s)
        dropped = len(signals) - len(deduped)
        if dropped:
            print(f"cooldown: dropped {dropped} repeat signals within "
                  f"{cfg.cooldown_days}d")
        signals = deduped

    # ---- CROSS-RUN cooldown: the tracker sheet remembers every alert, so a
    #      symbol alerted within cooldown_days does not re-alert on the NEXT
    #      run (2026-08-19 saw 5 identical re-alerts of the 08-18 signal:
    #      same setup, same last bar, no memory between runs) ----
    cooldown_skipped = 0
    if cfg.cooldown_days > 0 and cfg.tracker_enabled and signals:
        kept = []
        for s in signals:
            try:
                if recently_alerted(s["symbol"], str(s["signal_date"])[:10],
                                    cfg.cooldown_days, cfg.tracker_file):
                    cooldown_skipped += 1
                    continue
            except Exception:  # noqa: BLE001  (bad tracker file -> alert anyway)
                pass
            kept.append(s)
        if cooldown_skipped:
            print(f"cross-run cooldown: suppressed {cooldown_skipped} "
                  f"re-alert(s) (already alerted within {cfg.cooldown_days}d "
                  f"per {cfg.tracker_file})")
        signals = kept

    if near_misses:
        near_misses.sort(key=lambda x: -x[1])
        print(f"near-misses (pattern OK, score < {cfg.score_threshold:.0f}): "
              + ", ".join(f"{s}:{sc:.0f}" for s, sc, _ in near_misses[:10]))

    rows = _table_rows(sorted(signals, key=lambda s: -s["score"]))
    _print_table(rows)

    # ---- tracking sheet: log new signals + mark OPEN rows HIT/MISS ----
    if cfg.tracker_enabled:
        added = sum(1 for s in signals if log_signal(s, cfg.tracker_file))
        try:
            updated = update_open(cfg.tracker_file, client)
        except Exception as e:  # noqa: BLE001
            updated = 0
            print(f"WARNING: tracker update failed: {e}", file=sys.stderr)
        print(f"tracker: {added} new logged, {updated} OPEN -> HIT/MISS "
              f"({cfg.tracker_file})")

    if notifier is not None:
        scope = f"{len(symbols)} symbols"
        stats = (f"scanned {len(symbols)} · prefilter-skipped {pref_skipped} "
                 f"· errors {errors}")
        if cooldown_skipped:
            stats += f" · {cooldown_skipped} re-alert(s) suppressed by cooldown"
        if near_misses:
            stats += (" · near-misses (score < "
                      f"{cfg.score_threshold:.0f}): "
                      + ", ".join(f"{s} {sc:.0f}" for s, sc, _ in near_misses[:5]))
        sent = notifier.send_signals(signals, scope, stats)
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
    p.add_argument("--token", default=None, help="Dhan access token "
                   "(or set DHAN_ACCESS_TOKEN)")
    p.add_argument("--client-id", default=None, help="Dhan client id "
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
                   help="live: also fetch today's partial candle from Dhan "
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
            print("ERROR: Dhan access token required. Set DHAN_ACCESS_TOKEN "
                  "or pass --token (create one at Dhan -> Settings -> API).",
                  file=sys.stderr)
            return 2
        rows = run_live(cfg, token, client_id, args.limit, args.watchlist,
                        args.from_days, args.refresh, args.debug,
                        args.intraday, notifier)

    if args.out and rows:
        _write_csv(rows, args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
