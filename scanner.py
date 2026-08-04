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

from config import ScanConfig
from env_loader import load_env
from pattern import detect_setup
from telegram_notifier import TelegramNotifier


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
    b = {k: list(v) for k, v in bars.items() if isinstance(v, list)}
    b["dates"] = list(bars["dates"])
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
    from dhan_client import DhanClient
    from universes import get_universe

    client = DhanClient(token, client_id,
                        min_interval=cfg.request_interval,
                        timeout=cfg.api_timeout)

    # ---- universe: layered sources, NEVER hard-fails ----
    if watchlist:
        # explicit watchlist -> use it directly (user's choice)
        with open(watchlist) as f:
            symbols = [ln.strip().upper() for ln in f if ln.strip()
                       and not ln.strip().startswith("#")]
        source = f"watchlist ({len(symbols)} syms)"
        print(f"universe: {source}")
    else:
        symbols, source = get_universe()
        print(f"universe: {source}")
        if limit:
            symbols = symbols[:limit]
            print(f"limit: scanning first {limit} symbols")

    to_date = dt.date.today()
    from_date = to_date - dt.timedelta(days=from_days)
    today_s = to_date.isoformat()

    signals, errors = [], 0
    for i, sym in enumerate(symbols, 1):
        try:
            bars = client.get_daily(sym, from_date, to_date,
                                    force_refresh=force_refresh)
        except Exception as e:  # noqa: BLE001
            errors += 1
            if debug:
                print(f"  [{sym}] ERROR {e}")
            continue
        if bars is None or len(bars.get("close", [])) < cfg.min_bars:
            continue

        # ---- live market: append today's partial candle from intraday ----
        live_merged = False
        if intraday:
            last_d = bars["dates"][-1] if bars["dates"] else ""
            if last_d < today_s:
                try:
                    partial = client.intraday_partial(sym, to_date)
                except Exception:  # noqa: BLE001
                    partial = None
                if partial:
                    bars, live_merged = merge_partial(bars, partial, today_s)
            # if last_d == today_s, Dhan already gave today's forming bar

        dates = bars["dates"]
        sig = detect_setup(bars, dates, cfg)
        if sig:
            sig["intraday"] = bool(live_merged)
            signals.append(sig)
            tag = "LIVE " if live_merged else ""
            print(f"  SIGNAL {tag}{sym:12s} score={sig['score']:.0f} "
                  f"signal={sig['signal_date']} ssl={sig['ssl']:.1f} "
                  f"flush={sig['flush_date']}")
        if i % 50 == 0:
            print(f"  ... {i}/{len(symbols)} scanned ({len(signals)} signals, "
                  f"{errors} errors)")

    rows = _table_rows(sorted(signals, key=lambda s: -s["score"]))
    _print_table(rows)
    print(f"scanned {len(symbols)} symbols, {len(signals)} signals, {errors} errors")

    if notifier is not None:
        scope = f"{len(symbols)} symbols"
        sent = notifier.send_signals(signals, scope)
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
                   help="min score to report (default from config: 55)")
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
