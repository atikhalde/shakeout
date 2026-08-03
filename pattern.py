"""
The pattern detector: BOS -> failed tag -> flush to SSL -> hold -> reversal.

Given a bars dict (open/high/low/close/volume numpy arrays, oldest -> newest,
plus a parallel `dates` list), `detect_setup` looks at the LAST bar as the
candidate "reversal / retest" day and returns a structured signal dict if the
whole story matches, else None.

Visual reference of what we are detecting (3 verified examples):

  SPORTKING  : BOS 24-Jul (H 215.66 > 215.49 26W-high) -> peak 219.02
               -> flush 30-Jul to 194.99 (SSL 194.38, close held 196.50)
               -> reversal 31-Jul (+2.5%) -> +14% gap on 03-Aug
  BAJFINANCE : BOS 21-Jul (swing break, tags 26W-high 1074)
               -> flush 24-Jul to 1001.6 (SSL ~1000.5, close held 1012.6)
               -> reversal 27-Jul (+3.5%) -> +8% move by 31-Jul
  SPR AUTO   : BOS 21-Jul (swing break of 4417.8, tags 26W-high 4523)
               -> flush 24-Jul to 4126 (SSL ~4113, close held 4157)
               -> reversal 27-Jul (+2.0%) -> 4523 broken by 03-Aug
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from config import ScanConfig
from indicators import ema, rolling_max, rolling_min, avg_volume


# --------------------------------------------------------------------------
# BOS detection
# --------------------------------------------------------------------------

def _prev_highs(high: np.ndarray, lookback: int) -> np.ndarray:
    """max(high) over the `lookback` bars BEFORE each bar (NaN at start)."""
    out = np.full(len(high), np.nan, dtype=float)
    for i in range(lookback, len(high)):
        out[i] = np.max(high[i - lookback: i])
    return out


def _find_bos(high: np.ndarray, t: int, cfg: ScanConfig):
    """
    Latest BOS day in [t - bos_oldest, t - bos_newest].
    Returns (bos_day, style, break_level) or (None, None, None).
    """
    best = None  # (day, style, level)  -- latest day wins
    lo = t - cfg.bos_oldest
    hi = t - cfg.bos_newest
    if lo < 0:
        lo = 0

    styles = {"26w": cfg.bos_lookback_26w, "swing": cfg.bos_lookback_swing}
    valid = set(styles) | {"both"}
    if cfg.bos_style not in valid:
        raise ValueError(f"bos_style must be one of {valid}")
    if cfg.bos_style == "both":
        candidates = styles
    else:
        candidates = {cfg.bos_style: styles[cfg.bos_style]}

    for style, lb in candidates.items():
        prev = _prev_highs(high, lb)
        for d in range(hi, lo - 1, -1):  # newest first
            if np.isnan(prev[d]):
                continue
            if high[d] > prev[d] * (1 + cfg.bos_break_eps):
                if best is None or d > best[0]:
                    best = (d, style, float(prev[d]))
                break  # this style: take its newest BOS
    return best


# --------------------------------------------------------------------------
# Main detection
# --------------------------------------------------------------------------

def detect_setup(bars: dict, dates: list, cfg: ScanConfig) -> Optional[dict]:
    """
    bars : {'open','high','low','close','volume'} numpy arrays, oldest->newest
    dates: parallel python list (any comparable objects, used for reporting)
    Returns signal dict or None. Signal day = the LAST bar.
    """
    o, h, l, c = bars["open"], bars["high"], bars["low"], bars["close"]
    v = bars.get("volume", np.zeros(len(c)))
    n = len(c)
    if n < cfg.min_bars:
        return None

    t = n - 1  # candidate signal day = last completed bar

    # ------------------------------------------------------------- 1. BOS
    bos = _find_bos(h, t, cfg)
    if bos is None:
        return None
    bos_day, bos_style, break_level = bos

    # ------------------------------------------------------------- 2. flush
    peak_day = int(np.argmax(h[bos_day: t + 1])) + bos_day
    flush_day = int(np.argmin(l[bos_day: t + 1])) + bos_day
    peak = float(h[peak_day])
    flush_low = float(l[flush_day])

    if flush_day <= peak_day:            # the fall must come AFTER the peak
        return None
    drop = (peak - flush_low) / peak
    if drop < cfg.flush_min_drop:        # not a sudden fall
        return None

    # at least one clearly red session in the flush window (close-to-close),
    # i.e. real selling, not just a wide-spread doji day
    reds = []
    for i in range(peak_day + 1, t + 1):
        if c[i] < c[i - 1]:
            reds.append((c[i - 1] - c[i]) / c[i - 1])
    if not reds or max(reds) < cfg.flush_red_day_min:
        return None

    if t - flush_day > cfg.flush_max_age:  # flush must be fresh
        return None

    # ------------------------------------------------------------- 3. SSL
    lo = max(0, bos_day - cfg.ssl_pre_lookback)
    ssl = float(np.min(l[lo: bos_day]))
    if ssl <= 0:
        return None

    ratio = flush_low / ssl
    if ratio > 1 + cfg.ssl_tol_up or ratio < 1 - cfg.ssl_tol_dn:
        return None                       # never reached the SSL zone

    # ------------------------------------------------------------- 4. hold
    min_close_after = float(np.min(c[flush_day: t + 1]))
    if min_close_after <= ssl:            # closed below the SSL -> invalid
        return None

    # ------------------------------------------------------------- 5. reversal
    body = c[t] - o[t]
    rng = h[t] - l[t]
    if rng <= 0:
        return None
    if body <= 0:                         # last bar must be green
        return None
    if (c[t] - o[t]) / rng < cfg.body_ratio_min:
        return None
    if c[t] < c[t - 1] * (1 + cfg.bounce_min):
        return None
    if c[t] < ssl * cfg.near_ssl_close_min:
        return None

    # ------------------------------------------------------------- 6. before big move
    if c[t] >= peak:                      # already reclaimed the peak -> too late
        return None

    # ------------------------------------------------------------- filters
    if c[t] < cfg.min_price:
        return None
    if avg_volume(v, cfg.volume_lookback) < cfg.min_avg_volume:
        return None

    # ------------------------------------------------------------- assemble
    signal = {
        "symbol": bars.get("symbol", "?"),
        "signal_date": dates[t],
        "bos_date": dates[bos_day],
        "bos_style": bos_style,
        "break_level": break_level,
        "peak": peak,
        "peak_date": dates[peak_day],
        "flush_low": flush_low,
        "flush_date": dates[flush_day],
        "flush_drop_pct": drop * 100.0,
        "ssl": ssl,
        "min_close_after_ssl": min_close_after,
        "last_close": float(c[t]),
        "last_open": float(o[t]),
        "last_high": float(h[t]),
        "last_low": float(l[t]),
        "bounce_pct": (c[t] - c[t - 1]) / c[t - 1] * 100.0,
        "body_ratio": (c[t] - o[t]) / rng,
        "retrace_pct": (peak - flush_low) / max(peak - ssl, 1e-9) * 100.0,
        "days_since_bos": t - bos_day,
        "score": 0.0,
    }
    signal["score"] = _score(signal, bars, cfg)
    return signal


# --------------------------------------------------------------------------
# Scoring (0..100)
# --------------------------------------------------------------------------

def _score(sig: dict, bars: dict, cfg: ScanConfig) -> float:
    c = bars["close"]
    score = 0.0

    # freshness of the BOS (recent = better)
    score += 25.0 * max(0.0, 1.0 - sig["days_since_bos"] / cfg.bos_oldest)

    # depth of the flush (deeper shakeout relative to peak = better, cap 8%)
    score += 20.0 * min(1.0, sig["flush_drop_pct"] / 8.0)

    # how precisely the flush reached the SSL zone
    off = abs(sig["flush_low"] - sig["ssl"]) / sig["ssl"]
    tol = cfg.ssl_tol_up + cfg.ssl_tol_dn
    score += 20.0 * max(0.0, 1.0 - off / tol)

    # strength of the reversal bounce
    score += 20.0 * min(1.0, sig["bounce_pct"] / 5.0)

    # quality of the reversal candle body
    score += 15.0 * min(1.0, sig["body_ratio"] / 0.75)

    # trend intact bonus (soft)
    if len(c) >= 50:
        e20, e50 = ema(c, 20), ema(c, 50)
        if e20[-1] > e50[-1]:
            score += 5.0

    return round(min(score, 100.0), 1)
