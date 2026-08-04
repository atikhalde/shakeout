"""
Demo universe for the scanner: 3 verified positive cases + 7 negative cases.

Positives are reconstructed (pixel-level) from the actual TradingView charts
the user analysed - labelled with the exact date the scanner should have
flagged them (the reversal day, BEFORE the big move):

  SPORTKING  -> flag 2026-07-31  (big move 03-Aug: +14% gap to 241.5)
  BAJFINANCE -> flag 2026-07-27  (then +8% by 31-Jul, 52W high 1103 broken)
  SPR_AUTO   -> flag 2026-07-27  (4523 line broken on 03-Aug close 4524.4)

The visible tail of each series uses the real OHLC values recovered from the
chart images (cross-checked against the chart header and strategy panel).
Pre-history is synthetic but reproducible (fixed seeds).
"""

from __future__ import annotations

import numpy as np


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def _dates_ending(anchor: str, n: int) -> list[str]:
    """n business dates ending on `anchor` (inclusive)."""
    import datetime as _dt

    d = _dt.date.fromisoformat(anchor)
    out = []
    while len(out) < n:
        if d.weekday() < 5:  # Mon-Fri
            out.append(d.isoformat())
        d -= _dt.timedelta(days=1)
    return out[::-1]


def _pre_history(n, start_price, end_price, rng, vol=0.02, max_cap=None):
    """Deterministic drift + noise OHLC segment."""
    prices = np.linspace(start_price, end_price, n)
    noise = rng.normal(0, vol, n)
    c = prices * (1 + noise)
    o = np.roll(c, 1); o[0] = c[0] * (1 + rng.normal(0, vol))
    spread = np.abs(rng.normal(0, vol, n)) * prices
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    if max_cap is not None:
        h = np.minimum(h, max_cap)
    v = np.abs(rng.normal(1e6, 3e5, n))
    return o, h, l, c, v


def _concat(*segs):
    return tuple(np.concatenate([s[i] for s in segs]) for i in range(5))


def _rows(arr):
    """[(O,H,L,C,V)] -> numpy arrays."""
    a = np.array(arr, float)
    return (a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4])


def _make_bars(pre_segments, tail_rows):
    pre = _concat(*pre_segments)
    tail = _rows(tail_rows)
    return pre, tail


def _finalize(sym, pre, tail, anchor):
    o = np.concatenate([pre[0], tail[0]])
    h = np.concatenate([pre[1], tail[1]])
    l = np.concatenate([pre[2], tail[2]])
    c = np.concatenate([pre[3], tail[3]])
    v = np.concatenate([pre[4], tail[4]])
    dates = _dates_ending(anchor, len(c))
    bars = {"open": o, "high": h, "low": l, "close": c, "volume": v, "symbol": sym}
    return dates, bars


# ===========================================================================
# POSITIVE CASES
# ===========================================================================

def _sportking():
    rng = np.random.default_rng(11)
    base = [  # the whole base bottoms at 194.4 = the real SSL line
        (195.0, 197.5, 194.4, 196.5, 900_000),
        (196.5, 198.0, 195.0, 197.0, 850_000),
        (197.0, 199.0, 194.6, 198.0, 800_000),
        (198.0, 200.0, 195.0, 198.5, 820_000),
        (198.5, 200.5, 194.8, 199.0, 860_000),
        (199.0, 201.0, 195.2, 200.0, 900_000),
        (200.0, 202.0, 194.4, 201.0, 940_000),
        (201.0, 203.0, 195.4, 201.5, 910_000),
        (201.5, 203.5, 194.6, 202.0, 880_000),
        (202.0, 204.0, 195.0, 203.0, 920_000),
        (203.0, 205.0, 194.8, 203.5, 950_000),
        (203.5, 205.5, 195.2, 204.0, 980_000),
    ]
    run = [  # verified from the chart
        (198.34, 205.22, 198.34, 201.13, 1_500_000),   # 22-Jul
        (200.19, 214.33, 200.19, 207.14, 2_100_000),   # 23-Jul
        (205.86, 215.66, 204.59, 213.99, 2_400_000),   # 24-Jul
        (215.66, 218.69, 214.66, 215.66, 2_300_000),   # 27-Jul  BOS: H>215.49
        (216.67, 217.34, 209.07, 215.33, 1_900_000),   # 28-Jul
        (215.99, 219.03, 213.00, 213.99, 1_800_000),   # 29-Jul  peak 219.03
        (212.67, 215.99, 194.99, 196.50, 2_900_000),   # 30-Jul  FLUSH to SSL 194.4
        (196.81, 210.05, 196.81, 201.44, 2_600_000),   # 31-Jul  REVERSAL <- SIGNAL
    ]
    pre = _concat(_pre_history(60, 180, 215.6, rng, 0.015, max_cap=215.6),
                  _pre_history(60, 215.6, 150, rng, 0.020, max_cap=215.6),
                  _pre_history(55, 150, 197, rng, 0.010),
                  _rows(base))
    dates, bars = _finalize("SPORTKING", pre, _rows(run), "2026-07-31")
    return "SPORTKING", dates, bars, "2026-07-31"


def _bajfinance():
    rng = np.random.default_rng(22)
    base = [  # lows bottom at 999.3 (real SSL zone ~1000.5)
        (1000.0, 1005.0, 999.3, 1002.0, 2_400_000),
        (1002.0, 1006.0, 1000.0, 1003.5, 2_300_000),
        (1003.5, 1007.0, 1000.5, 1005.0, 2_200_000),
        (1005.0, 1008.0, 1001.0, 1006.0, 2_300_000),
        (1006.0, 1010.0, 1001.5, 1008.0, 2_400_000),
        (1008.0, 1012.0, 1002.0, 1009.0, 2_500_000),
        (1009.0, 1013.0, 1002.5, 1010.0, 2_450_000),
        (1010.0, 1014.0, 1003.0, 1011.0, 2_400_000),
        (1011.0, 1015.0, 1003.5, 1012.0, 2_500_000),
        (1012.0, 1016.0, 1004.0, 1013.0, 2_600_000),
    ]
    run = [  # verified from the chart
        (1056.5, 1067.5, 1038.5, 1063.8, 3_200_000),   # 20-Jul
        (1063.8, 1073.6, 1061.4, 1068.1, 3_400_000),   # 21-Jul BOS(swing)+tag 1074
        (1063.8, 1069.9, 1056.5, 1060.2, 2_800_000),   # 22-Jul fails
        (1052.9, 1060.8, 1036.7, 1039.7, 3_000_000),   # 23-Jul falls
        (1027.2, 1030.2, 1001.6, 1012.6, 5_500_000),   # 24-Jul FLUSH to SSL
        (1026.6, 1049.9, 1026.6, 1048.1, 3_800_000),   # 27-Jul REVERSAL <- SIGNAL
    ]
    pre = _concat(_pre_history(80, 980, 1074, rng, 0.010, max_cap=1075),
                  _pre_history(60, 1074, 950, rng, 0.016, max_cap=1075),
                  _pre_history(55, 950, 995, rng, 0.006),
                  _rows(base))
    dates, bars = _finalize("BAJFINANCE", pre, _rows(run), "2026-07-27")
    return "BAJFINANCE", dates, bars, "2026-07-27"


def _spr_auto():
    rng = np.random.default_rng(33)
    base = [  # lows bottom at 4102 (real SSL ~4113)
        (4102, 4140, 4102, 4120, 300_000),
        (4120, 4150, 4105, 4135, 310_000),
        (4135, 4165, 4108, 4145, 320_000),
        (4145, 4175, 4110, 4150, 330_000),
        (4150, 4180, 4113, 4160, 340_000),
        (4160, 4190, 4115, 4170, 350_000),
        (4170, 4200, 4118, 4180, 360_000),
        (4180, 4210, 4120, 4190, 370_000),
        (4190, 4220, 4122, 4200, 380_000),
        (4200, 4230, 4125, 4210, 390_000),
    ]
    run = [  # verified from the chart
        (4169.0, 4241.0, 4169.0, 4228.6, 420_000),     # 20-Jul
        (4241.0, 4520.0, 4241.0, 4472.0, 980_000),     # 21-Jul BOS(swing)+tag 4523
        (4505.0, 4520.0, 4334.0, 4384.0, 900_000),     # 22-Jul double tag, fail
        (4368.0, 4368.0, 4261.0, 4277.0, 640_000),     # 23-Jul fall
        (4261.0, 4269.0, 4126.0, 4157.0, 830_000),     # 24-Jul FLUSH to SSL 4102
        (4169.0, 4281.0, 4169.0, 4241.0, 590_000),     # 27-Jul REVERSAL <- SIGNAL
    ]
    pre = _concat(_pre_history(80, 3800, 4523, rng, 0.012, max_cap=4525),
                  _pre_history(60, 4523, 3600, rng, 0.018, max_cap=4525),
                  _pre_history(55, 3600, 4100, rng, 0.006),
                  _rows(base))
    dates, bars = _finalize("SPR_AUTO", pre, _rows(run), "2026-07-27")
    return "SPR_AUTO", dates, bars, "2026-07-27"


# ===========================================================================
# NEGATIVE CASES
# ===========================================================================

def _neg_runner():
    rng = np.random.default_rng(44)
    pre = _concat(_pre_history(80, 160, 205, rng, 0.012, max_cap=210),
                  _pre_history(70, 205, 178, rng, 0.015),
                  _pre_history(20, 178, 182, rng, 0.008))
    rows = [
        (180.0, 184.0, 179.0, 182.0, 900_000),
        (183.0, 212.0, 182.0, 209.0, 1_400_000),   # BOS
        (210.0, 224.0, 209.0, 222.0, 1_600_000),
        (223.0, 238.0, 222.0, 236.0, 1_800_000),
        (237.0, 249.0, 236.0, 247.0, 2_000_000),
        (248.0, 260.0, 247.0, 258.0, 2_200_000),   # peak on last bar, no flush
    ]
    dates, bars = _finalize("NEG_RUNNER", pre, _rows(rows), "2026-07-31")
    return "NEG_RUNNER", dates, bars, None


def _neg_ssl_break():
    rng = np.random.default_rng(55)
    base = [(181.0, 184.0, 181.0, 182.5, 800_000)] * 12
    pre = _concat(_pre_history(80, 160, 205, rng, 0.012, max_cap=210),
                  _pre_history(70, 205, 178, rng, 0.015),
                  _rows(base))
    rows = [
        (182.0, 186.0, 181.0, 184.0, 900_000),
        (185.0, 214.0, 184.0, 211.0, 1_400_000),   # BOS
        (212.0, 213.0, 200.0, 201.0, 1_300_000),
        (200.0, 201.0, 188.0, 189.0, 1_500_000),
        (188.0, 189.0, 179.0, 179.8, 1_700_000),   # CLOSES below SSL 181
        (180.0, 185.0, 179.5, 184.0, 900_000),     # bounce, but SSL was broken
    ]
    dates, bars = _finalize("NEG_SSL_BREAK", pre, _rows(rows), "2026-07-31")
    return "NEG_SSL_BREAK", dates, bars, None


def _neg_no_bos():
    rng = np.random.default_rng(66)
    o, h, l, c, v = _pre_history(170, 190, 190, rng, 0.020, max_cap=208)
    rows = [
        (192.0, 195.0, 188.0, 189.0, 800_000),
        (189.0, 193.0, 185.0, 186.0, 800_000),
        (186.0, 190.0, 183.0, 184.0, 800_000),
        (184.0, 188.0, 181.0, 182.0, 800_000),
        (182.0, 196.0, 181.5, 195.0, 900_000),    # bounce, but NO BOS before
    ]
    pre = (o, h, l, c, v)
    dates, bars = _finalize("NEG_NO_BOS", pre, _rows(rows), "2026-07-31")
    return "NEG_NO_BOS", dates, bars, None


def _neg_post_move():
    rng = np.random.default_rng(77)
    base = [(195.0, 197.5, 194.4, 196.5, 900_000)] * 10
    pre = _concat(_pre_history(60, 180, 215.6, rng, 0.015, max_cap=215.6),
                  _pre_history(60, 215.6, 150, rng, 0.020, max_cap=215.6),
                  _pre_history(55, 150, 197, rng, 0.010),
                  _rows(base))
    rows = [
        (198.34, 205.22, 198.34, 201.13, 1_500_000),
        (200.19, 214.33, 200.19, 207.14, 2_100_000),
        (205.86, 215.66, 204.59, 213.99, 2_400_000),
        (215.66, 218.69, 214.66, 215.66, 2_300_000),
        (216.67, 217.34, 209.07, 215.33, 1_900_000),
        (215.99, 219.03, 213.00, 213.99, 1_800_000),
        (212.67, 215.99, 194.99, 196.50, 2_900_000),   # flush
        (196.81, 210.05, 196.81, 201.44, 2_600_000),   # reversal 31-Jul
        (225.99, 241.51, 220.10, 229.63, 4_500_000),   # 03-Aug big move ALREADY
    ]
    dates, bars = _finalize("NEG_POST_MOVE", pre, _rows(rows), "2026-08-03")
    return "NEG_POST_MOVE", dates, bars, None


def _neg_still_falling():
    rng = np.random.default_rng(88)
    base = [(195.0, 197.5, 194.4, 196.5, 900_000)] * 10
    pre = _concat(_pre_history(60, 180, 215.6, rng, 0.015, max_cap=215.6),
                  _pre_history(60, 215.6, 150, rng, 0.020, max_cap=215.6),
                  _pre_history(55, 150, 197, rng, 0.010),
                  _rows(base))
    rows = [
        (198.34, 205.22, 198.34, 201.13, 1_500_000),
        (200.19, 214.33, 200.19, 207.14, 2_100_000),
        (205.86, 215.66, 204.59, 213.99, 2_400_000),
        (215.66, 218.69, 214.66, 215.66, 2_300_000),
        (216.67, 217.34, 209.07, 215.33, 1_900_000),
        (215.99, 219.03, 213.00, 213.99, 1_800_000),
        (212.67, 215.99, 194.99, 196.50, 2_900_000),   # flush day - still red
    ]
    dates, bars = _finalize("NEG_STILL_FALLING", pre, _rows(rows), "2026-07-30")
    return "NEG_STILL_FALLING", dates, bars, None


def _neg_shallow_pullback():
    rng = np.random.default_rng(99)
    base = [(195.0, 197.5, 194.4, 196.5, 900_000)] * 10
    pre = _concat(_pre_history(60, 180, 215.6, rng, 0.015, max_cap=215.6),
                  _pre_history(60, 215.6, 150, rng, 0.020, max_cap=215.6),
                  _pre_history(55, 150, 197, rng, 0.010),
                  _rows(base))
    rows = [
        (198.34, 205.22, 198.34, 201.13, 1_500_000),
        (200.19, 214.33, 200.19, 207.14, 2_100_000),
        (205.86, 215.66, 204.59, 213.99, 2_400_000),
        (215.66, 218.69, 214.66, 215.66, 2_300_000),
        (216.67, 217.34, 209.07, 215.33, 1_900_000),
        (215.99, 219.03, 213.00, 213.99, 1_800_000),
        (212.67, 214.50, 207.80, 208.50, 1_600_000),   # only -5%, low ~208
        (208.50, 212.00, 208.00, 211.00, 1_400_000),   # bounce, SSL never tested
    ]
    dates, bars = _finalize("NEG_SHALLOW", pre, _rows(rows), "2026-07-31")
    return "NEG_SHALLOW", dates, bars, None


def _neg_stale_bos():
    rng = np.random.default_rng(100)
    pre = _concat(_pre_history(60, 180, 215.6, rng, 0.015, max_cap=215.6),
                  _pre_history(60, 215.6, 150, rng, 0.020, max_cap=215.6),
                  _pre_history(30, 150, 193, rng, 0.010))
    rows = [
        (210.0, 219.0, 209.0, 215.5, 1_400_000),   # BOS 14 bars before end
        (215.5, 216.0, 211.0, 212.0, 1_200_000),
        (212.0, 213.0, 208.0, 209.0, 1_100_000),
        (209.0, 210.0, 205.0, 206.0, 1_000_000),
        (206.0, 207.0, 202.0, 203.0, 950_000),
        (203.0, 204.0, 200.0, 201.0, 900_000),
        (201.0, 202.0, 198.0, 199.0, 880_000),
        (199.0, 200.0, 196.0, 197.0, 860_000),
        (197.0, 198.0, 195.5, 196.0, 840_000),
        (196.0, 197.0, 194.5, 195.0, 820_000),
        (195.0, 196.0, 193.5, 194.0, 810_000),
        (194.0, 195.0, 193.0, 193.5, 800_000),
        (193.5, 194.5, 192.5, 193.0, 790_000),
        (193.0, 194.0, 192.0, 192.5, 780_000),
        (192.5, 197.0, 192.5, 196.5, 900_000),     # bounce, but BOS too old
    ]
    dates, bars = _finalize("NEG_STALE", pre, _rows(rows), "2026-07-31")
    return "NEG_STALE", dates, bars, None


def demo_universe() -> dict:
    """symbol -> (dates, bars, expected_signal_date_or_None)"""
    out = {}
    for fn in (_sportking, _bajfinance, _spr_auto, _neg_runner, _neg_ssl_break,
               _neg_no_bos, _neg_post_move, _neg_still_falling,
               _neg_shallow_pullback, _neg_stale_bos):
        sym, dates, bars, expected = fn()
        bars["symbol"] = sym
        out[sym] = (dates, bars, expected)
    return out


if __name__ == "__main__":
    for sym, (dates, bars, exp) in demo_universe().items():
        print(f"{sym:16s} bars={len(bars['close']):4d} last={dates[-1]} expected={exp}")
