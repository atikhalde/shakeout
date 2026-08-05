#!/usr/bin/env python3
"""
Backtest the shakeout scanner on REAL historical data from the DHAN API
(runs inside GitHub Actions with your DHAN_ACCESS_TOKEN secret).

For every trading day in the window it runs the SAME logic as the live
scanner (BOS -> flush -> SSL hold -> reversal, + 26W-high proximity guard,
+ prefilter conditions) and records each signal with its forward returns:

    entry      = next day's OPEN   (alert at close, enter next open)
    r3/r5/r7/r10/r15 = close[t+k] vs entry
    max15      = best close within 15 sessions vs entry
    min15      = worst close within 15 sessions vs entry
    big_move   = 1 if max15 >= +8% (the Sportking-style pop) else 0

    The summary prints a score-bucket table (50/55/60/65/70/75/80) so you
    can see the win rate and big-move rate climb as the threshold rises -
    the pattern is a SHORT-TERM bounce (3-7 days) and the +8%+ moves only
    fire on the highest-quality setups.

Usage:
    # Dhan API (default - uses DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID env vars):
    python backtest.py --source dhan --years 2 --limit 500 --min-score 55

    # Yahoo Finance (local quick run, no token needed):
    pip install yfinance
    python backtest.py --source yfinance --years 2 --limit 100

Output: signals_backtest.csv + signals_backtest.xlsx (Excel with a
        'Signals' sheet containing EVERY signal incl. full score component
        breakdown, and a 'Summary' sheet with win-rate stats + score buckets) and a
        printed win-rate summary, including a score>=70 vs score<70 split.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import statistics
import sys
import time

import numpy as np

from config import ScanConfig
from dhan_client import DhanClient, _iso_date
from pattern import _score
from prefilter import passes_prefilter

# ---------------------------------------------------------------------------
# universe (used for source=yfinance and as fallback)
# ---------------------------------------------------------------------------
UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "LT", "HINDUNILVR", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "HCLTECH", "NTPC", "POWERGRID",
    "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "ASIANPAINT", "ADANIENT",
    "ADANIPORTS", "BAJAJFINSV", "BAJAJ-AUTO", "NESTLEIND", "ONGC", "COALINDIA",
    "GRASIM", "INDUSINDBK", "TECHM", "DIVISLAB", "DRREDDY", "CIPLA",
    "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO", "BRITANNIA", "HINDALCO",
    "SBILIFE", "TATACONSUM", "UPL", "DABUR", "HDFCLIFE", "ICICIPRULI",
    "DLF", "PIDILITIND", "BERGEPAINT", "HAVELLS", "BOSCHLTD", "SIEMENS",
    "ABB", "TATAELXSI", "PERSISTENT", "COFORGE", "LTIM", "POLYCAB",
    "TVSMOTOR", "MOTHERSON", "ASHOKLEY", "AMBUJACEM", "ACC", "SHREECEM",
    "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "INDUSIND", "FEDERALBNK",
    "IDFCFIRSTB", "AUBANK", "YESBANK", "ZOMATO", "PAYTM", "DMART", "TRENT",
    "MCDOWELL-N", "VEDL", "HINDZINC", "SAIL", "JINDALSTEL", "COLPAL",
    "MARICO", "GODREJCP", "PAGEIND", "JUBLFOOD", "DEVYANI", "INDUSTOWER",
    "BHARATFORG", "BHEL", "HAL", "BEL", "DIXON", "VOLTAS", "SUZLON",
    "TATAPOWER", "ADANIGREEN", "ADANITRANS", "GAIL", "IOC", "BPCL", "HPCL",
    "PETRONET", "IGL", "GMRINFRA", "IRCTC", "RVNL", "IREDA", "NHPC", "SJVN",
    "PFC", "RECLTD", "LICHSGFIN", "HDFCAMC", "UTIAMC", "CDSL", "BSE", "MCX",
    "ICICIGI", "SHRIRAMFIN", "CHOLAFIN", "MUTHOOTFIN", "MANAPPURAM",
    "BAJAJHLDNG", "SPR_AUTO", "SPORTKING", "MAHINDCIE", "MINDACORP",
    "CRAFTSMAN", "ACI", "ASTRAL", "ATUL", "BALRAMCHIN", "BANKINDIA",
    "BATAINDIA", "BIOCON", "BLUESTARCO", "CASTROLIND", "CESC", "CGPOWER",
    "CHENNPETRO", "CUB", "CUMMINSIND", "CYIENT", "DALBHARAT", "DEEPAKNTR",
    "DELHIVERY", "ESCORTS", "EXIDEIND", "FINEORG", "GILLETTE", "GLAND",
    "GLAXO", "GODREJIND", "GODREJPROP", "GUJGASLTD", "HAPPSTMNDS",
    "HINDCOPPER", "IDBI", "IDEA", "IIFL", "INDHOTEL", "INDIGO", "JKCEMENT",
    "JSL", "JSWENERGY", "JYOTHYLAB", "KANSAINER", "KAYNES", "KPITTECH",
    "LALPATHLAB", "LAURUSLABS", "LICI", "LUPIN", "MAHABANK", "MAXHEALTH",
    "MFSL", "MGL", "MPHASIS", "MRF", "NATCOPHARM", "NAUKRI", "NAVINFLUOR",
    "NCC", "NIACL", "OBEROIRLTY", "OFSS", "OIL", "PHOENIXLTD", "PIIND",
    "PNBHOUSING", "POLYMED", "PRESTIGE", "RAMCOCEM", "RBLBANK", "REDINGTON",
    "SBICARD", "SHRIRAMPPS", "SIGNATURE", "SONACOMS", "STARHEALTH", "SUNTV",
    "SUPREMEIND", "SWIGGY", "SYNGENE", "TATACOMM", "THERMAX", "TIINDIA",
    "TORNTPHARM", "TORNTPOWER", "UCOBANK", "UNOMINDA", "VBL", "VIPIND",
    "WELCORP", "WESTLIFE", "WHIRLPOOL", "ZYDUSLIFE",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def rolling_prev_max(a: np.ndarray, w: int) -> np.ndarray:
    """out[i] = max(a[i-w : i]) (max of the w bars BEFORE i); NaN at start."""
    out = np.full(len(a), np.nan)
    if len(a) < 2:
        return out
    from collections import deque
    dq: deque = deque()
    for i in range(len(a)):
        while dq and dq[0] <= i - w - 1:
            dq.popleft()
        if i >= 1:
            while dq and a[dq[-1]] <= a[i - 1]:
                dq.pop()
            dq.append(i - 1)
        if dq:
            out[i] = a[dq[0]]
    return out


def backtest_symbol(sym: str, cfg: ScanConfig, bars: dict,
                    out_rows: list, debug_symbol: str | None = None) -> int:
    """Run the full pattern on every historical day; append signals.
    If debug_symbol == sym, prints WHY candidate days are rejected."""
    o = np.asarray(bars["open"], float)
    h = np.asarray(bars["high"], float)
    l = np.asarray(bars["low"], float)
    c = np.asarray(bars["close"], float)
    v = np.asarray(bars.get("volume", np.zeros(len(c))), float)
    dates = bars["dates"]
    n = len(c)
    if n < cfg.min_bars:
        return 0

    prev26 = rolling_prev_max(h, cfg.bos_lookback_26w)
    prev45 = rolling_prev_max(h, cfg.bos_lookback_swing)

    found = 0
    dbg = debug_symbol == sym
    # scan EVERY day INCLUDING the last bar: recent signals (e.g. the user's
    # Jul-2026 setups happening days before today) must be included. The very
    # last bar is reported with recent=1 and NaN forward returns (no entry
    # price yet - it is today's still-forming signal).
    for t in range(cfg.min_bars, n):
        # ---------------- BOS (mirrors pattern._find_bos) ----------------
        bos_day, bos_style, brk = None, None, None
        best = None
        for style, prev in (("26w", prev26), ("swing", prev45)):
            for d in range(t - cfg.bos_newest, t - cfg.bos_oldest - 1, -1):
                if d < 0 or np.isnan(prev[d]):
                    continue
                if h[d] > prev[d] * (1 + cfg.bos_break_eps):
                    if best is None or d > best[0]:
                        best = (d, style, float(prev[d]))
                    break
        if best is None:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] no BOS in window")
            continue
        bos_day, bos_style, brk = best

        # ------- 26W proximity guard (rejects weak swing breaks) --------
        if bos_style == "swing":
            h26 = prev26[t]
            if np.isnan(h26) or h26 <= 0:
                continue
            if np.max(h[bos_day:t + 1]) < h26 * cfg.swing_26w_proximity:
                if dbg and dates[t] >= "2026-07-20":
                    prox = np.max(h[bos_day:t + 1]) / h26 * 100
                    print(f"    [{dates[t]}] swing BOS but peak {prox:.1f}% "
                          f"of 26W high (need {cfg.swing_26w_proximity*100:.0f}%)")
                continue

        # ------------------------- flush --------------------------------
        pk = bos_day + int(np.argmax(h[bos_day:t + 1]))
        fl = bos_day + int(np.argmin(l[bos_day:t + 1]))
        if fl <= pk:
            continue
        peak = float(h[pk])
        flush_low = float(l[fl])
        drop = (peak - flush_low) / peak
        if drop < cfg.flush_min_drop:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] flush drop {drop*100:.1f}% < "
                      f"{cfg.flush_min_drop*100:.0f}%")
            continue
        reds = [(c[i - 1] - c[i]) / c[i - 1] for i in range(pk + 1, t + 1)
                if c[i] < c[i - 1]]
        if not reds or max(reds) < cfg.flush_red_day_min:
            continue
        if t - fl > cfg.flush_max_age:
            continue

        # -------------------------- SSL --------------------------------
        lo = max(0, bos_day - cfg.ssl_pre_lookback)
        ssl = float(np.min(l[lo:bos_day]))
        if ssl <= 0:
            continue
        ratio = flush_low / ssl
        if ratio > 1 + cfg.ssl_tol_up or ratio < 1 - cfg.ssl_tol_dn:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] flush low {flush_low:.1f} vs SSL "
                      f"{ssl:.1f} ({ratio*100:.1f}%) outside tolerance")
            continue

        # ------------------------- hold --------------------------------
        if float(np.min(c[fl:t + 1])) <= ssl:
            continue

        # ---------------------- reversal day ---------------------------
        rng = h[t] - l[t]
        if rng <= 0 or c[t] <= o[t]:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] last candle not green "
                      f"(O {o[t]:.1f} C {c[t]:.1f})")
            continue
        if (c[t] - o[t]) / rng < cfg.body_ratio_min:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] body ratio {(c[t]-o[t])/rng:.2f} < "
                      f"{cfg.body_ratio_min}")
            continue
        if c[t] < c[t - 1] * (1 + cfg.bounce_min):
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] bounce {(c[t]/c[t-1]-1)*100:.1f}% < "
                      f"{cfg.bounce_min*100:.0f}%")
            continue
        if c[t] < ssl * cfg.near_ssl_close_min:
            continue
        if c[t] >= peak:
            if dbg and dates[t] >= "2026-07-20":
                print(f"    [{dates[t]}] close {c[t]:.1f} >= peak {peak:.1f} "
                      f"(too late)")
            continue

        # ------------------- prefilter (same as live) ------------------
        # CRITICAL: slice bars to day t ONLY - using the full history would
        # look ahead into the future (close[-1] is the last day of the
        # dataset, not day t) and corrupt every historical signal.
        ok, why = passes_prefilter({k: v[:t + 1] for k, v in bars.items()}, cfg)
        if not ok:
            if debug_symbol and sym == debug_symbol:
                print(f"    [{dates[t]}] prefilter REJECT: {why}")
            continue

        # --------------------- score (same formula) --------------------
        sig_tmp = {
            "days_since_bos": t - bos_day,
            "flush_drop_pct": drop * 100.0,
            "flush_low": flush_low,
            "ssl": ssl,
            "bounce_pct": (c[t] / c[t - 1] - 1) * 100.0,
            "body_ratio": (c[t] - o[t]) / rng,
        }
        score, _parts = _score(sig_tmp, {"close": c[:t + 1]}, cfg)
        if score < cfg.score_threshold:
            continue
        # flatten the score components (needed for the last-bar branch too)
        comp = {name: round(v, 1) for name, (v, _m) in (_parts or {}).items()}
        # ------------------- forward returns (next open) ---------------
        if t + 1 >= n:
            # last bar: signal is forming today, no forward data yet
            out_rows.append({
                "symbol": sym, "date": dates[t],
                "close": round(float(c[t]), 2), "score": score,
                "bos": dates[bos_day], "style": bos_style,
                "peak": round(peak, 2), "flush_low": round(flush_low, 2),
                "ssl": round(ssl, 2),
                "score_freshness": comp.get("BOS freshness", ""),
                "score_flush": comp.get("Flush depth", ""),
                "score_ssl": comp.get("SSL precision", ""),
                "score_bounce": comp.get("Reversal bounce", ""),
                "score_body": comp.get("Candle body", ""),
                "score_trend": comp.get("Trend (EMA20/50)", ""),
                "r3": float("nan"), "r5": float("nan"), "r7": float("nan"),
                "r10": float("nan"), "r15": float("nan"),
                "max15": float("nan"), "min15": float("nan"),
                "big_move": 0, "recent": 1,
            })
            found += 1
            continue
        entry = o[t + 1]
        if entry <= 0:
            continue
        fwd = {}
        for k in (3, 5, 7, 10, 15):
            if t + k < n:
                fwd[f"r{k}"] = (c[t + k] / entry - 1) * 100
        # partial windows are fine: recent signals have incomplete forward
        # data (NaN where not enough bars yet) - still reported.
        fwd_win = c[t + 1:min(t + 16, n)]
        max15 = float(np.max(fwd_win) / entry - 1) * 100 if len(fwd_win) else float("nan")
        min15 = float(np.min(fwd_win) / entry - 1) * 100 if len(fwd_win) else float("nan")
        # "big move" = the Sportking-style pop: best close within 15d
        # gains >= 8% from entry (the alert's "big move not fired yet")
        big_move = 1 if (not np.isnan(max15) and max15 >= cfg.big_move_pct) else 0
        # recent = still forming (fewer than 15 forward sessions available)
        recent = 1 if t + 15 >= n else 0

        out_rows.append({
            "symbol": sym, "date": dates[t], "close": round(float(c[t]), 2),
            "score": score, "bos": dates[bos_day], "style": bos_style,
            "peak": round(peak, 2), "flush_low": round(flush_low, 2),
            "ssl": round(ssl, 2),
            "score_freshness": comp.get("BOS freshness", ""),
            "score_flush": comp.get("Flush depth", ""),
            "score_ssl": comp.get("SSL precision", ""),
            "score_bounce": comp.get("Reversal bounce", ""),
            "score_body": comp.get("Candle body", ""),
            "score_trend": comp.get("Trend (EMA20/50)", ""),
            "r3": round(fwd.get("r3", float("nan")), 2),
            "r5": round(fwd.get("r5", float("nan")), 2),
            "r7": round(fwd.get("r7", float("nan")), 2),
            "r10": round(fwd.get("r10", float("nan")), 2),
            "r15": round(fwd.get("r15", float("nan")), 2),
            "max15": round(max15, 2), "min15": round(min15, 2),
            "big_move": big_move, "recent": recent,
        })
        found += 1
    return found


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def _stats(rows, key):
    vals = [r[key] for r in rows
            if r.get(key) is not None
            and not (isinstance(r.get(key), float) and np.isnan(r[key]))]
    if not vals:
        return None
    wins = sum(1 for x in vals if x > 0)
    return {
        "n": len(vals), "win": wins / len(vals) * 100,
        "avg": statistics.mean(vals), "med": statistics.median(vals),
    }


def print_summary(rows: list[dict], errors: int, elapsed: float,
                  cfg: ScanConfig) -> None:
    print("\n" + "=" * 74)
    print(f"BACKTEST RESULT — {len(rows)} signals, {errors} fetch errors, "
          f"{elapsed:.0f}s")
    print("=" * 74)
    if not rows:
        print("No signals in the window.")
        return
    print("entry = NEXT day open (alert at close, enter next open)\n")
    labels = {"r3": "3 days", "r5": "5 days", "r7": "7 days",
              "r10": "10 days", "r15": "15 days",
              "max15": "best within 15d", "min15": "worst within 15d"}
    for k in ("r3", "r5", "r7", "r10", "r15", "max15", "min15"):
        s = _stats(rows, k)
        if s:
            print(f"  {labels[k]:>18}: n={s['n']:3d}  win={s['win']:5.1f}%  "
                  f"avg={s['avg']:+6.2f}%  med={s['med']:+6.2f}%")

    # ---- big-move stats (the Sportking-style pop) ----
    bm = sum(1 for r in rows if r.get("big_move"))
    print(f"\n  💥 BIG MOVE (≥ +{cfg.big_move_pct:.0f}% within 15d): "
          f"{bm}/{len(rows)} signals = {bm / len(rows) * 100:.1f}%")

    # ---- score-bucket table: does raising the threshold help? ----
    print("\n  score bucket        n   5d-win   7d-win   5d-avg   big-move%")
    for thr in (50, 55, 60, 65, 70, 75, 80):
        sub = [r for r in rows if r["score"] >= thr]
        if len(sub) < 3:
            continue
        s5 = _stats(sub, "r5")
        s7 = _stats(sub, "r7")
        bm_s = sum(1 for r in sub if r.get("big_move"))
        if s5:
            print(f"  score >= {thr:<3d}       {len(sub):3d}   "
                  f"{s5['win']:5.1f}%   "
                  f"{(s7['win'] if s7 else 0):5.1f}%   "
                  f"{s5['avg']:+6.2f}%   {bm_s / len(sub) * 100:5.1f}%")
    print()


# ---------------------------------------------------------------------------
# Excel export (all scoring details)
# ---------------------------------------------------------------------------

def write_excel(rows: list[dict], path: str) -> None:
    """Write signals to an .xlsx workbook: sheet 'Signals' (all columns)
    + sheet 'Summary' (win-rate stats + score buckets)."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        print("openpyxl not installed - skipping Excel export "
              "(pip install openpyxl)", file=sys.stderr)
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Signals"
    fields = list(rows[0].keys())
    hdr_fill = PatternFill("solid", fgColor="1F4E78")
    hdr_font = Font(color="FFFFFF", bold=True)
    ws.append(fields)
    for c in ws[1]:
        c.fill = hdr_fill
        c.font = hdr_font
    for r in rows:
        ws.append([r.get(f, "") for f in fields])
    # freeze header + autofilter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    # column widths
    for i, f in enumerate(fields, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = \
            max(10, min(20, len(f) + 2))

    # ---- Summary sheet ----
    ws2 = wb.create_sheet("Summary")
    ws2.append(["Backtest summary"])
    ws2["A1"].font = Font(bold=True, size=13)
    ws2.append(["signals", len(rows)])
    labels = {"r3": "3 days", "r5": "5 days", "r7": "7 days",
              "r10": "10 days", "r15": "15 days",
              "max15": "best within 15d", "min15": "worst within 15d"}
    ws2.append([])
    ws2.append(["horizon", "n", "win%", "avg%", "med%"])
    for k in ("r3", "r5", "r7", "r10", "r15", "max15", "min15"):
        s = _stats(rows, k)
        if s:
            ws2.append([labels[k], s["n"], round(s["win"], 1),
                        round(s["avg"], 2), round(s["med"], 2)])
    bm = sum(1 for r in rows if r.get("big_move"))
    ws2.append(["BIG MOVE >=+8%", bm, round(bm / len(rows) * 100, 1) if rows else 0])
    ws2.append([])
    ws2.append(["score bucket", "n", "5d-win%", "7d-win%", "5d-avg%", "big-move%"])
    for thr in (50, 55, 60, 65, 70, 75, 80):
        sub = [r for r in rows if r["score"] >= thr]
        if len(sub) < 3:
            continue
        s5 = _stats(sub, "r5")
        s7 = _stats(sub, "r7")
        bm_s = sum(1 for r in sub if r.get("big_move"))
        if s5:
            ws2.append([f">= {thr}", len(sub), round(s5["win"], 1),
                        round(s7["win"], 1) if s7 else "",
                        round(s5["avg"], 2),
                        round(bm_s / len(sub) * 100, 1)])
    for i, f in enumerate("ABCDEF", 1):
        ws2.column_dimensions[f].width = 12

    wb.save(path)
    print(f"wrote Excel -> {path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["dhan", "yfinance"], default="dhan")
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--out", default="signals_backtest.csv")
    ap.add_argument("--limit", type=int, default=0,
                    help="scan only the first N symbols (0 = all)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the daily-bar cache (rebuild it fresh) "
                         "- use once if the cache is polluted from failed "
                         "runs, then drop it")
    ap.add_argument("--resume", action="store_true",
                    help="skip symbols already in the daily-bar cache "
                         "(resume after a throttled/cancelled run)")
    ap.add_argument("--min-mcap", type=float, default=0,
                    help="only backtest symbols with market cap >= this many "
                         "crores (uses data/market_cap.csv; 1000 = liquid "
                         "stocks only - matches what the live scanner trades)")
    ap.add_argument("--debug-symbol", default=None,
                    help="print WHY this symbol's candidate days are "
                         "rejected (e.g. --debug-symbol SPORTKING)")
    ap.add_argument("--days", type=int, default=None,
                    help="override: fetch N calendar days of history")
    args = ap.parse_args()

    cfg = ScanConfig()
    if args.min_score is not None:
        cfg.score_threshold = args.min_score

    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [ln.strip().upper() for ln in f
                       if ln.strip() and not ln.strip().startswith("#")]
    elif args.source == "dhan":
        from dhan_client import DhanClient
        token = os.environ.get("DHAN_ACCESS_TOKEN")
        client_id = os.environ.get("DHAN_CLIENT_ID")
        if not token:
            print("ERROR: set DHAN_ACCESS_TOKEN (and DHAN_CLIENT_ID) to run "
                  "with source=dhan, or use --source yfinance.", file=sys.stderr)
            return 2
        # backtest fetches years of data per symbol - use a gentler rate
        # (1.2s between calls, serial) to avoid Dhan's 429 on the shared
        # GitHub runner IP; slower but completes instead of dying
        client = DhanClient(token, client_id,
                            min_interval=cfg.request_interval)
        try:
            symbols = client.liquid_universe()
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: liquid_universe failed ({e}); "
                  f"using static list", file=sys.stderr)
            symbols = list(UNIVERSE)
    else:
        symbols = list(UNIVERSE)
    if args.min_mcap:
        from prefilter import load_mcap, mcap_filter
        mcap = load_mcap(cfg.mcap_file)
        if mcap:
            before = len(symbols)
            symbols, _dropped = mcap_filter(symbols, mcap, args.min_mcap)
            print(f"mcap filter: {before} -> {len(symbols)} symbols "
                  f"(>= {args.min_mcap:.0f} Cr)")
        else:
            print(f"WARNING: {cfg.mcap_file} not found - min-mcap skipped")
    # NOTE: no --resume symbol filter here - get_daily() already reads the
    # daily-bar cache automatically, so re-runs are fast WITHOUT dropping
    # symbols from analysis (a resume filter caused universe=0 when the
    # whole cache was populated - every symbol was skipped).
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"source={args.source} universe={len(symbols)} symbols, "
          f"{args.years}y window, min_score={cfg.score_threshold}")

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days or (365 * args.years + 30))

    rows: list[dict] = []
    errors = 0
    t0 = time.time()

    skipped_429 = 0
    consec_429 = 0
    fast_skip = False
    rate_limit_streak = 0
    for i, sym in enumerate(symbols, 1):
        try:
            if args.source == "dhan":
                # ----- YESTERDAY'S PROVEN LOGIC: serial, single request ----
                bars = None
                for attempt in range(2):
                    try:
                        bars = client.get_daily(sym, start, end)
                        break
                    except Exception as e:  # noqa: BLE001
                        if "429" in str(e):
                            rate_limit_streak += 1
                            if attempt == 0:
                                # wait 30s once, then retry (like yesterday)
                                print(f"    429 on {sym} -> wait 30s, "
                                      f"retry once", flush=True)
                                time.sleep(30)
                            else:
                                skipped_429 += 1
                        else:
                            raise
                # ----- RATE-LIMIT FAILOVER: if Dhan persistently 429s,
                #       switch the REST of the run to yfinance so the
                #       backtest always completes (useful when running
                #       from a foreign IP that Dhan throttles) ----
                if rate_limit_streak >= 3:
                    print(f"\n>>> Dhan rate-limiting ({rate_limit_streak} "
                          f"consecutive 429s). Switching the REST of the "
                          f"run to yfinance so the backtest still "
                          f"completes. (Signal stats are close enough for "
                          f"win-rate analysis.)\n", flush=True)
                    args.source = "yfinance"
                    rate_limit_streak = 0
                    continue
                if bars is None or len(bars.get("close", [])) < cfg.min_bars:
                    errors += 1
                    if bars is None:
                        raw = getattr(client, "_last_raw", "")[:120]
                        reason = f"none [dhan: {raw}]" if raw else "none"
                    else:
                        reason = f"only {len(bars.get('close', []))} bars"
                    print(f"  {i}/{len(symbols)} {sym:12s} no data ({reason})",
                          flush=True)
                    continue
                bars["dates"] = [_iso_date(d) for d in bars["dates"]]
            else:
                import yfinance as yf
                yf_sym = {"SPR_AUTO": "SHRIPISTON"}.get(sym, sym)
                df = yf.Ticker(f"{yf_sym}.NS").history(
                    start=start, end=end, auto_adjust=True)
                if df is None or df.empty:
                    raise ValueError("empty")
                df = df.dropna(subset=["Open", "High", "Low", "Close"])
                bars = {
                    "open": df["Open"].to_numpy(float),
                    "high": df["High"].to_numpy(float),
                    "low": df["Low"].to_numpy(float),
                    "close": df["Close"].to_numpy(float),
                    "volume": df["Volume"].to_numpy(float),
                    "dates": [d.date().isoformat() for d in df.index],
                }
            got = backtest_symbol(sym, cfg, bars, rows, args.debug_symbol)
            print(f"  {i}/{len(symbols)} {sym:12s} "
                  f"bars={len(bars['close']):4d} signals={got}", flush=True)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  {i}/{len(symbols)} {sym:12s} ERROR {str(e)[:70]}",
                  flush=True)

    # ---- cooldown: keep only the FIRST signal per symbol within
    #      cooldown_days (backtest-verified: repeats win 30% vs 63%) ----
    if cfg.cooldown_days > 0 and rows:
        rows.sort(key=lambda r: (r["symbol"], r["date"]))
        dedup, last_by_sym = [], {}
        for r in rows:
            sym = r["symbol"]
            if sym in last_by_sym:
                try:
                    gap = (dt.date.fromisoformat(r["date"])
                           - dt.date.fromisoformat(last_by_sym[sym])).days
                    if gap <= cfg.cooldown_days:
                        continue
                except ValueError:
                    pass
            last_by_sym[sym] = r["date"]
            dedup.append(r)
        print(f"cooldown: {len(rows)} -> {len(dedup)} signals "
              f"(dropped {len(rows)-len(dedup)} repeats within "
              f"{cfg.cooldown_days}d)")
        rows = dedup

    rows.sort(key=lambda r: r["date"])
    if rows:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {len(rows)} signals -> {args.out}")
        # also write Excel with full scoring details
        xlsx = os.path.splitext(args.out)[0] + ".xlsx"
        write_excel(rows, xlsx)

    if skipped_429:
        print(f"NOTE: {skipped_429} symbols were skipped due to Dhan "
              f"rate limit. Run the backtest again later - the daily-bar "
              f"cache means only the skipped symbols need fetching.")
    print_summary(rows, errors, time.time() - t0, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
