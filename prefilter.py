"""
Pre-filter that narrows the universe BEFORE the pattern scan, using the
conditions from the strategy panel (same as the TradingView checklist):

    1. Weekly RSI(14) > 60
    2. Weekly MACD histogram (26, 12, 9) > 0
    3. Daily close > 100
    4. Market cap > 1000 Cr        (from data/market_cap.csv)
    5. Daily close > daily open    (today's candle is green)

Two-stage usage:
  * mcap_filter(symbols)   -- called BEFORE fetching any bars (kills ~80%
                              of the API calls)
  * passes_prefilter(bars) -- called AFTER fetching a symbol's bars (cheap
                              CPU check; skips pattern detection for weak
                              candidates)
"""

from __future__ import annotations

import csv
import os

import numpy as np

from config import ScanConfig


# --------------------------------------------------------------------------
# indicators (weekly-aggregated)
# --------------------------------------------------------------------------

def _weekly_closes(close: np.ndarray) -> np.ndarray:
    """Group daily closes into 5-trading-day (approx weekly) buckets ->
    last close of each bucket."""
    n = len(close)
    k = n // 5
    if k == 0:
        return close
    trimmed = close[n - k * 5:]
    return trimmed.reshape(k, 5)[:, -1]


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """Wilder RSI on the last value."""
    if len(closes) < period + 1:
        return float("nan")
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _macd_hist(closes: np.ndarray, fast: int = 12, slow: int = 26,
               signal: int = 9) -> float:
    """MACD histogram (last value) on weekly closes: (EMA12-EMA26) - EMA9."""
    if len(closes) < slow + signal:
        return float("nan")
    macd = _ema(closes, fast) - _ema(closes, slow)
    sig = _ema(macd, signal)
    return float(macd[-1] - sig[-1])


# --------------------------------------------------------------------------
# market cap
# --------------------------------------------------------------------------

def load_mcap(path: str = "data/market_cap.csv") -> dict[str, float] | None:
    """symbol -> mcap_cr. None if the file is missing (filter then skipped)."""
    if not os.path.exists(path):
        return None
    out: dict[str, float] = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    out[row["symbol"].strip().upper()] = float(row["mcap_cr"])
                except (ValueError, KeyError):
                    continue
    except OSError:
        return None
    return out or None


def mcap_filter(symbols: list[str], mcap: dict[str, float] | None,
                min_cr: float = 1000.0) -> tuple[list[str], int]:
    """
    Keep only symbols with market cap >= min_cr crores.
    Unknown symbols (not present in the mcap map) are KEPT - the filter only
    drops symbols that are explicitly known to be below the threshold, so a
    partial market-cap file can never accidentally kill valid candidates.
    If `mcap` is None (no data), the filter is skipped -> returns everything.
    Returns (kept_symbols, dropped_count).
    """
    if mcap is None:
        return symbols, 0
    kept = [s for s in symbols
            if s not in mcap or mcap.get(s, 0.0) >= min_cr]
    return kept, len(symbols) - len(kept)


# --------------------------------------------------------------------------
# main prefilter
# --------------------------------------------------------------------------

def passes_prefilter(bars: dict, cfg: ScanConfig) -> tuple[bool, str]:
    """Check the panel conditions on a symbol's daily bars (no extra API
    calls). Returns (pass, reason_if_failed)."""
    c = np.asarray(bars["close"], float)
    o = np.asarray(bars["open"], float)
    if len(c) < 60:
        return False, "insufficient history"

    # 3) daily close > 100
    if cfg.prefilter_close_min and c[-1] < cfg.prefilter_close_min:
        return False, f"close {c[-1]:.0f} < {cfg.prefilter_close_min:.0f}"

    # 5) today's candle green (close > open)
    if cfg.prefilter_green_daily and c[-1] <= o[-1]:
        return False, "daily candle red"

    wk = _weekly_closes(c)

    # 1) weekly RSI(14) > 60
    if cfg.prefilter_rsi_min is not None:
        r = _rsi(wk, 14)
        if not (r > cfg.prefilter_rsi_min):
            return False, f"weekly RSI {r:.1f} <= {cfg.prefilter_rsi_min}"

    # 2) weekly MACD histogram (slow=26, fast=12, signal=9) > 0
    if cfg.prefilter_macd_min is not None:
        mh = _macd_hist(wk)  # defaults: fast=12, slow=26, signal=9
        if not (mh > cfg.prefilter_macd_min):
            return False, f"weekly MACD hist {mh:.2f} <= {cfg.prefilter_macd_min}"

    return True, ""
