"""Small, dependency-light technical indicator helpers (pure numpy)."""

from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average (same formula as TradingView)."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def sma(values: np.ndarray, window: int) -> np.ndarray:
    """Simple moving average; NaN-filled at the start."""
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < window:
        return out
    cs = np.cumsum(values.astype(float))
    out[window - 1:] = (cs[window - 1:] - np.concatenate([[0.0], cs[:-window]])) / window
    return out


def rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    """rolling maximum; positions with insufficient history are NaN."""
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(window - 1, len(values)):
        out[i] = np.max(values[i - window + 1: i + 1])
    return out


def rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    """rolling minimum; positions with insufficient history are NaN."""
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(window - 1, len(values)):
        out[i] = np.min(values[i - window + 1: i + 1])
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, span: int = 14) -> np.ndarray:
    """Average True Range (Wilder-style approximation via EMA)."""
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low,
                    np.maximum(np.abs(high - prev_close),
                               np.abs(low - prev_close)))
    return ema(tr, span)


def avg_volume(volume: np.ndarray, window: int = 20) -> float:
    if len(volume) < window:
        return float(np.mean(volume)) if len(volume) else 0.0
    return float(np.mean(volume[-window:]))
