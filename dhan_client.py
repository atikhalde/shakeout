"""
Minimal Dhan API v2 client for the scanner.

Docs:  https://dhanhq.co/docs/v2/
Auth:  create an app at Dhan -> Settings -> API, then use the access token.

Headers used:  `access-token` (required) and `client-id` (set if provided).

Endpoints used:
  GET /v2/instruments?exchangeSegment=NSE_EQ          -> CSV of all NSE symbols
  GET /v2/charts/historical?symbol=<SYM>&exchangeSegment=NSE_EQ
        &instrumentType=1&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD -> daily OHLC

Daily bars are cached to CSV files under <cache_dir> so re-runs are fast and
the number of API calls (and the chance of hitting rate limits) is minimal.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import threading
import time
from typing import Optional

import requests

BASE_URL = "https://api.dhan.co"


class DhanClient:
    def __init__(self, token: str, client_id: Optional[str] = None,
                 cache_dir: str = "data/cache",
                 min_interval: float = 0.15, timeout: float = 20.0,
                 max_retries: int = 3):
        self.token = token
        self.client_id = client_id
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_call = 0.0
        self._lock = threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict) -> requests.Response:
        headers = {
            "access-token": self.token,
            "Accept": "application/json, text/csv",
        }
        if self.client_id:
            headers["client-id"] = self.client_id

        with self._lock:
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

        last_err = None
        for attempt in range(self.max_retries):
            try:
                r = requests.get(
                    BASE_URL + path, params=params, headers=headers,
                    timeout=self.timeout,
                )
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise last_err if last_err else RuntimeError("request failed")

    # ------------------------------------------------------------ instruments
    def get_nse_equity_symbols(self) -> list[str]:
        """
        Full NSE equity universe from Dhan's instrument master CSV.
        Returns plain trading symbols, e.g. ['RELIANCE', 'TCS', ...].
        """
        r = self._get("/v2/instruments", {"exchangeSegment": "NSE_EQ"})
        r.encoding = "utf-8"
        symbols = []
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            seg = (row.get("SEM_SEGMENT") or "").strip()
            expiry = (row.get("SEM_EXPIRY_DATE") or "").strip()
            if seg != "NSE_EQ" or expiry:
                continue
            sym = (row.get("SEM_TRADING_SYMBOL") or
                   row.get("SEM_INSTRUMENT_NAME") or "").strip()
            if sym and not any(ch.isdigit() for ch in sym.split()[-1:]):
                symbols.append(sym)
        return sorted(set(symbols))

    # ----------------------------------------------------------------- OHLC
    def get_daily(self, symbol: str, from_date: dt.date, to_date: dt.date,
                  force_refresh: bool = False) -> Optional[dict]:
        """
        Daily bars for `symbol` between from_date..to_date (inclusive).
        Returns dict with 'symbol', 'dates', 'open','high','low','close','volume'
        (oldest -> newest) or None if no data / invalid symbol.
        """
        cache_file = os.path.join(self.cache_dir, f"{symbol}.csv")

        if not force_refresh and os.path.exists(cache_file):
            bars = self._read_cache(cache_file)
            if bars and bars["dates"] and bars["dates"][-1] >= to_date:
                return self._slice(bars, from_date, to_date)

        r = self._get("/v2/charts/historical", {
            "symbol": symbol,
            "exchangeSegment": "NSE_EQ",
            "instrumentType": "1",   # 1 = equity
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
        })
        bars = self._parse_ohlc(r)
        if bars is None:
            return None
        bars["symbol"] = symbol
        self._write_cache(cache_file, bars)
        return self._slice(bars, from_date, to_date)

    # ------------------------------------------------------------- parsing
    def _parse_ohlc(self, r: requests.Response) -> Optional[dict]:
        """Tolerant parser: Dhan may return columnar JSON or a row list."""
        data = r.json()
        if isinstance(data, dict):
            inner = data.get("data") if isinstance(data.get("data"), (dict, list)) else data
        else:
            inner = data

        if isinstance(inner, dict):
            keys = ("open", "high", "low", "close", "volume", "timestamp")
            if all(k in inner for k in keys):
                return {
                    "dates": [str(x)[:10] for x in inner["timestamp"]],
                    "open": inner["open"], "high": inner["high"],
                    "low": inner["low"], "close": inner["close"],
                    "volume": inner.get("volume", [0] * len(inner["close"])),
                }
            # nested: {'data': {'open': [...], ...}}
            if "open" in inner and "close" in inner:
                keys = ("open", "high", "low", "close", "volume")
                ts = inner.get("timestamp") or inner.get("date") or inner.get("dates")
                if not ts:
                    return None
                return {
                    "dates": [str(x)[:10] for x in ts],
                    "open": inner["open"], "high": inner["high"],
                    "low": inner["low"], "close": inner["close"],
                    "volume": inner.get("volume", [0] * len(inner["close"])),
                }

        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            row0 = inner[0]
            if all(k in row0 for k in ("open", "high", "low", "close")):
                rows = []
                for row in inner:
                    try:
                        rows.append({
                            "dates": str(row.get("timestamp") or row.get("date"))[:10],
                            "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"]),
                            "volume": float(row.get("volume", 0) or 0),
                        })
                    except (TypeError, ValueError, KeyError):
                        continue
                if rows:
                    rows.sort(key=lambda x: x["dates"])
                    return {k: [x[k] for x in rows] for k in
                            ("dates", "open", "high", "low", "close", "volume")}
        return None

    # ------------------------------------------------------------- caching
    @staticmethod
    def _read_cache(path: str) -> Optional[dict]:
        try:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            if not rows:
                return None
            return {
                "dates": [r["date"] for r in rows],
                "open": [float(r["open"]) for r in rows],
                "high": [float(r["high"]) for r in rows],
                "low": [float(r["low"]) for r in rows],
                "close": [float(r["close"]) for r in rows],
                "volume": [float(r.get("volume", 0) or 0) for r in rows],
            }
        except Exception:
            return None

    @staticmethod
    def _write_cache(path: str, bars: dict) -> None:
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                for i in range(len(bars["dates"])):
                    w.writerow([bars["dates"][i], bars["open"][i], bars["high"][i],
                                bars["low"][i], bars["close"][i], bars["volume"][i]])
        except Exception:
            pass

    @staticmethod
    def _slice(bars: dict, from_date: dt.date, to_date: dt.date) -> dict:
        lo, hi = 0, len(bars["dates"])
        for i, d in enumerate(bars["dates"]):
            if d >= from_date.isoformat():
                lo = i
                break
        for i, d in enumerate(bars["dates"]):
            if d > to_date.isoformat():
                hi = i
                break
        return {k: (v[lo:hi] if isinstance(v, list) else v)
                for k, v in bars.items()}


# --------------------------------------------------------------------------
# token helpers
# --------------------------------------------------------------------------

def token_from_env() -> tuple[Optional[str], Optional[str]]:
    return (os.environ.get("DHAN_ACCESS_TOKEN"),
            os.environ.get("DHAN_CLIENT_ID"))
