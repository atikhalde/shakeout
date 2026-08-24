#!/usr/bin/env python3
"""
CONTROL TEST: reconstruct the user's real 2026-08-18 signal from the numbers
in their Telegram alert and verify the CURRENT (post-fix) code still fires it.

Alert facts encoded:
  BOS 2026-08-10, style 26w, broke 1151.85
  Peak 1159.72 on 2026-08-10
  Flush low 1013.70 on 2026-08-14 (-12.6% from peak)
  SSL 999.63 (min low of 7 bars before BOS)
  closes held >= 1092.90
  Reversal 2026-08-18: close 1130.60, +3.4% vs prev close (~1093.40),
  body ratio 0.72, volume 1.36x
Expected: score ~74, parts 12/25, 20/20, 9/20, 14/20, 14/15, 5/5.
"""
import datetime as dt
import math
import numpy as np

from config import ScanConfig
from pattern import detect_setup
from prefilter import passes_prefilter, _weekly_closes, _rsi, _macd_hist
from telegram_notifier import TelegramNotifier


def build_runup(n_pre: int, end_price: float) -> list:
    """Realistic multi-month run-up into a 26W high, ending near the base."""
    # generate n_pre WEEKDAYS ENDING on 2026-08-07 (Friday before the BOS Mon)
    days = []
    d = dt.date(2026, 8, 7)
    while len(days) < n_pre:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    days.reverse()
    assert days[-1] == dt.date(2026, 8, 7), days[-1]
    bars = []
    for i in range(n_pre):
        frac = i / (n_pre - 1)
        base = 950 + (end_price - 950) * frac ** 1.15
        noise = math.sin(i * 0.35) * 7 + (4 if i % 7 == 0 else -5)
        cl = base + noise
        op = cl - 3 + (i % 3)
        hi = max(op, cl) + 5 + (7 if i % 11 == 0 else 0)
        lo = min(op, cl) - 5
        bars.append([days[i], op, hi, lo, cl, 400_000 + (i % 5) * 25_000])
    return bars


def make_bars(flush_close_drop: float = 0.024,
              reversal_pct: float = 0.034,
              hist_end: float = 1148.0):
    n_pre = 178
    pre = build_runup(n_pre, hist_end)
    # plant the exact 26W high 3 weeks before the BOS
    hi_i = n_pre - 16
    pre[hi_i] = [pre[hi_i][0], 1140.0, 1151.85, 1138.0, 1146.0, 900_000]
    # rows = pre[:n_pre-6] + 7 tail bars, so the BOS day is row n_pre-6 and
    # the 7 pre-BOS bars (used for the SSL) are pre[n_pre-13 .. n_pre-7]
    for j in range(7):
        idx = n_pre - 13 + j
        cl = 1050 + j * 1.5
        pre[idx] = [pre[idx][0], cl - 4, cl + 6, cl - 9, cl, 500_000]
    ssl_i = n_pre - 10
    pre[ssl_i] = [pre[ssl_i][0], 1024.0, 1030.0, 999.63, 1020.0, 800_000]

    prev_close_before_flush = 1120.0
    flush_close = round(prev_close_before_flush * (1 - flush_close_drop), 2)
    aug17_close = round(1130.60 / (1 + reversal_pct), 2)
    tail = [
        (dt.date(2026, 8, 10), 1062.0, 1159.72, 1058.0, 1150.0, 2_500_000),
        (dt.date(2026, 8, 11), 1150.0, 1156.0, 1138.0, 1141.0, 1_200_000),
        (dt.date(2026, 8, 12), 1140.0, 1143.0, 1122.0, 1125.0, 1_400_000),
        (dt.date(2026, 8, 13), 1124.0, 1128.0, 1108.0, prev_close_before_flush, 1_500_000),
        (dt.date(2026, 8, 14), 1118.0, 1122.0, 1013.70, flush_close, 3_600_000),
        (dt.date(2026, 8, 17), aug17_close - 4, aug17_close + 12, aug17_close - 8,
         aug17_close, 1_100_000),
        (dt.date(2026, 8, 18), 1122.25, 1133.6, 1122.0, 1130.60, 1_360_000),
    ]
    rows = pre[: n_pre - 6] + [list(t) for t in tail]
    return {
        "symbol": "MYSTOCK",
        "dates": [r[0].isoformat() for r in rows],
        "open": np.array([r[1] for r in rows], float),
        "high": np.array([r[2] for r in rows], float),
        "low": np.array([r[3] for r in rows], float),
        "close": np.array([r[4] for r in rows], float),
        "volume": np.array([r[5] for r in rows], float),
    }


def main():
    cfg = ScanConfig()
    bars = make_bars()
    print(f"bars={len(bars['dates'])}  last={bars['dates'][-1]}")
    sig = detect_setup(bars, bars["dates"], cfg)
    assert sig, "REGRESSION: current code does NOT fire the Aug-18 setup!"
    print(f"\nFIRED  {sig['symbol']}  {sig['signal_date']}  score {sig['score']}")
    for name, (val, mx) in sig["score_parts"].items():
        print(f"   {name:18s} {val:5.1f}/{mx:.0f}")
    print(f"   ssl={sig['ssl']:.2f} flush_low={sig['flush_low']:.2f} "
          f"drop={sig['flush_drop_pct']:.1f}% bounce={sig['bounce_pct']:.2f}% "
          f"body={sig['body_ratio']:.2f}")
    print(f"   plan: stop {sig['stop_level']:.2f} target {sig['target_level']:.2f} "
          f"rr {sig['rr']:.2f}  strong_reversal={sig['strong_reversal']}")

    msg = TelegramNotifier.format_signal(sig)
    print("\n--- alert preview (first lines) ---")
    print("\n".join(msg.splitlines()[:8]))

    # ---- prefilter verdict for the SAME bars (what run_live applies first)
    ok, why = passes_prefilter(bars, cfg)
    wk = _weekly_closes(bars["close"])
    print(f"\nprefilter on the exact Aug-18 setup: pass={ok}  ({why})")
    print(f"   weekly RSI(14)  = {_rsi(wk, 14):.1f}   (needs > {cfg.prefilter_rsi_min})")
    print(f"   weekly MACD hist= {_macd_hist(wk):.2f}  (needs > {cfg.prefilter_macd_min})")

    # ---- how the prefilter verdict shifts as the flush deepens
    print("\n--- prefilter vs flush depth (same shape, deeper shakeouts) ---")
    print("   flush drop% | weekly RSI | MACD hist | prefilter | pattern score")
    for drop, fclose in ((0.05, 1141.0), (0.06, 1141.0), (0.08, 1141.0),
                         (0.10, 1141.0), (0.126, 1092.90)):
        b = make_bars(flush_close_drop=0.024 if drop == 0.126 else 0.024)
        # vary the flush depth via the low + close of Aug 14
        lowv = 1159.72 * (1 - drop)
        i14 = b["dates"].index("2026-08-14")
        b["low"][i14] = lowv
        b["close"][i14] = fclose if drop == 0.126 else min(fclose, lowv * 1.08)
        ok, why = passes_prefilter(b, cfg)
        s = detect_setup(b, b["dates"], cfg)
        wk = _weekly_closes(b["close"])
        r = _rsi(wk, 14)
        m = _macd_hist(wk)
        print(f"     {drop*100:5.1f}%     |  {r:6.1f}   | {m:8.2f}  | "
              f"{'PASS' if ok else 'FAIL':8s}  | "
              f"{s['score'] if s else '-'}")


if __name__ == "__main__":
    main()
