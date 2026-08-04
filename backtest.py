#!/usr/bin/env python3
"""
Backtest the shakeout scanner on REAL historical data from the DHAN API
(runs inside GitHub Actions with your DHAN_ACCESS_TOKEN secret).

For every trading day in the window it runs the SAME logic as the live
scanner (BOS -> flush -> SSL hold -> reversal, + 26W-high proximity guard,
+ prefilter conditions) and records each signal with its forward returns:

    entry      = next day's OPEN   (alert at close, enter next open)
    r3/r5/r10  = close[t+k] vs entry
    max15      = best close within 15 sessions vs entry
    min15      = worst close within 15 sessions vs entry

Usage:
    # Dhan API (default - uses DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID env vars):
    python backtest.py --source dhan --years 2 --limit 500 --min-score 55

    # Yahoo Finance (local quick run, no token needed):
    pip install yfinance
    python backtest.py --source yfinance --years 2 --limit 100

Output: signals_backtest.csv (every signal + forward returns) and a
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
                    out_rows: list) -> int:
    """Run the full pattern on every historical day; append signals."""
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
    last_ok = n - 16  # need 15 forward sessions to measure returns

    found = 0
    for t in range(cfg.min_bars, last_ok):
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
            continue
        bos_day, bos_style, brk = best

        # ------- 26W proximity guard (rejects weak swing breaks) --------
        if bos_style == "swing":
            h26 = prev26[t]
            if np.isnan(h26) or h26 <= 0:
                continue
            if np.max(h[bos_day:t + 1]) < h26 * cfg.swing_26w_proximity:
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
            continue

        # ------------------------- hold --------------------------------
        if float(np.min(c[fl:t + 1])) <= ssl:
            continue

        # ---------------------- reversal day ---------------------------
        rng = h[t] - l[t]
        if rng <= 0 or c[t] <= o[t]:
            continue
        if (c[t] - o[t]) / rng < cfg.body_ratio_min:
            continue
        if c[t] < c[t - 1] * (1 + cfg.bounce_min):
            continue
        if c[t] < ssl * cfg.near_ssl_close_min:
            continue
        if c[t] >= peak:
            continue

        # ------------------- prefilter (same as live) ------------------
        ok, _ = passes_prefilter(bars, cfg)
        if not ok:
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

        # ------------------- forward returns (next open) ---------------
        entry = o[t + 1]
        if entry <= 0:
            continue
        fwd = {}
        for k in (3, 5, 10, 15):
            if t + k < n:
                fwd[f"r{k}"] = (c[t + k] / entry - 1) * 100
        max15 = float(np.max(c[t + 1:t + 16]) / entry - 1) * 100
        min15 = float(np.min(c[t + 1:t + 16]) / entry - 1) * 100

        out_rows.append({
            "symbol": sym, "date": dates[t], "close": round(float(c[t]), 2),
            "score": score, "bos": dates[bos_day], "style": bos_style,
            "peak": round(peak, 2), "flush_low": round(flush_low, 2),
            "ssl": round(ssl, 2),
            "r3": round(fwd.get("r3", float("nan")), 2),
            "r5": round(fwd.get("r5", float("nan")), 2),
            "r10": round(fwd.get("r10", float("nan")), 2),
            "r15": round(fwd.get("r15", float("nan")), 2),
            "max15": round(max15, 2), "min15": round(min15, 2),
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


def print_summary(rows: list[dict], errors: int, elapsed: float) -> None:
    print("\n" + "=" * 74)
    print(f"BACKTEST RESULT — {len(rows)} signals, {errors} fetch errors, "
          f"{elapsed:.0f}s")
    print("=" * 74)
    if not rows:
        print("No signals in the window.")
        return
    print("entry = NEXT day open (alert at close, enter next open)\n")
    labels = {"r3": "3 days", "r5": "5 days", "r10": "10 days",
              "r15": "15 days", "max15": "best within 15d",
              "min15": "worst within 15d"}
    for k in ("r3", "r5", "r10", "r15", "max15", "min15"):
        s = _stats(rows, k)
        if s:
            print(f"  {labels[k]:>18}: n={s['n']:3d}  win={s['win']:5.1f}%  "
                  f"avg={s['avg']:+6.2f}%  med={s['med']:+6.2f}%")

    # score bucket split (does a higher threshold improve results?)
    for thr in (60, 70, 80):
        sub = [r for r in rows if r["score"] >= thr]
        s = _stats(sub, "r5")
        if s and s["n"] >= 3:
            print(f"\n  score >= {thr}: {s['n']} signals -> 5-day win "
                  f"{s['win']:.1f}% avg {s['avg']:+.2f}%")
    print()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["dhan", "yfinance"], default="dhan")
    ap.add_argument("--symbols-file", default=None)
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--out", default="signals_backtest.csv")
    ap.add_argument("--limit", type=int, default=0,
                    help="scan only the first N symbols (0 = all)")
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
        client = DhanClient(token, client_id)
        try:
            symbols = client.liquid_universe()
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: liquid_universe failed ({e}); "
                  f"using static list", file=sys.stderr)
            symbols = list(UNIVERSE)
    else:
        symbols = list(UNIVERSE)
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"source={args.source} universe={len(symbols)} symbols, "
          f"{args.years}y window, min_score={cfg.score_threshold}")

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days or (365 * args.years + 30))

    rows: list[dict] = []
    errors = 0
    t0 = time.time()

    for i, sym in enumerate(symbols, 1):
        try:
            if args.source == "dhan":
                bars = client.get_daily(sym, start, end)
                if bars is None or len(bars.get("close", [])) < cfg.min_bars:
                    errors += 1
                    continue
                # ensure ISO dates (Dhan may return epoch)
                bars["dates"] = [str(d)[:10] for d in bars["dates"]]
            else:
                import yfinance as yf
                df = yf.Ticker(f"{sym}.NS").history(
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
            got = backtest_symbol(sym, cfg, bars, rows)
            print(f"  {i}/{len(symbols)} {sym:12s} bars={len(bars['close']):4d} "
                  f"signals={got}", flush=True)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  {i}/{len(symbols)} {sym:12s} ERROR {str(e)[:70]}")
            time.sleep(1)
        time.sleep(0.25)

    rows.sort(key=lambda r: r["date"])
    if rows:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {len(rows)} signals -> {args.out}")

    print_summary(rows, errors, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
