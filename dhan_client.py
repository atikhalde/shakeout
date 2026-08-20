"""
Minimal Dhan API v2 client for the scanner - CORRECT endpoint format.

Verified against the official dhanhq 2.2.0 Python client:

  * Scrip master  : GET  https://images.dhan.co/api-data/api-scrip-master.csv
                    (compact, ~25 MB, contains SEM_SMST_SECURITY_ID and
                     SEM_TRADING_SYMBOL; NSE equities = SEM_EXM_EXCH_ID=='NSE'
                     & SEM_SEGMENT=='E' & no expiry date)
  * Historical    : POST https://api.dhan.co/v2/charts/historical
                    form-encoded JSON body with securityId, exchangeSegment,
                    instrument, expiryCode, oi, fromDate, toDate, dhanClientId
  * Intraday      : POST https://api.dhan.co/v2/charts/intraday
                    (same params + interval)
  * Auth headers  : access-token + client-id (both required)

Responses: {"data": {"open": [...], "high": [...], ...}} (columnar arrays).

Daily bars are cached to CSV files under <cache_dir>; the instrument map is
cached under <cache_dir>/instruments_map.csv and refreshed daily.
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

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
BASE_URL = "https://api.dhan.co"
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 "
                   "Safari/537.36"),
    "Referer": "https://dhanhq.co/",
}

# TradingView-style symbols that differ from the exchange master symbol
SYMBOL_ALIASES = {
    "SPR_AUTO": "SHRIPISTON",   # Shriram Pistons renamed to SPR Auto
}

# ---------------------------------------------------------------------------
# ETF / fund exclusion (Dhan marks ETFs with SEM_SERIES == 'EQ' too, so we
# must filter by name). Any symbol containing one of these tokens, or exactly
# matching one of EXACT_ETF_NAMES, is excluded - EXCEPT the whitelisted real
# stocks whose names collide (GOLDIAM, JETFREIGHT, ALPHAGEO, BALPHARMA).
# ---------------------------------------------------------------------------
ETF_EXCLUDE_TOKENS = (
    "NIFTY", "SENSEX", "BEES", "IETF", "ETF", "SETF", "GOLD", "SILV",
    "LIQUID", "SHARIA", "MOMENT", "QUALITY", "VALUE", "GROWTH", "GROWW",
    "CASE", "ADD", "BETA", "BND", "ALPHA", "NEXT50", "NV20", "MID150",
    "SMALL250", "SML250", "PSUB", "PRIVATEBANK", "FINNIFTY", "REALTY",
    "100ESG", "100MOM", "MIDMOM", "MIDCAP", "SMALLCAP", "MIDSMALL",
    "TOP10", "TOP15", "TOP20", "TOP30", "TOP50", "TOP100", "MOM100",
    "MOM50", "MOMID", "MOMMID", "CONSUMP", "LICNET", "LICNMID", "LICMF",
    "ILIQ", "NSETEST",
)
ETF_EXACT_NAMES = {
    "INFRA", "METAL", "VALUE", "HEALTH", "MIDCAP", "SMALLCAP", "MIDSMALL",
    "ALPHA", "BETA", "CASE", "ADD", "BND", "LIQUID", "GOLD", "SILVER",
    "NEXT50", "NV20", "MOMENTUM", "MOM100", "MOM50", "PSUBANK", "FINNIFTY",
    "PRIVATEBANK", "QUALITY30", "TOP20", "TOP50", "REALTY", "SENSEX",
    "NIFTY50", "NIFTY100", "NIFTY500", "GOLD1", "SILVER1", "LIQUID1",
    "MID150", "SMALL250", "SML250",
}
ETF_WHITELIST_REAL = {"JETFREIGHT", "GOLDIAM", "ALPHAGEO", "BALPHARMA"}


def _iso_date(ts) -> str:
    """Normalize a Dhan timestamp (epoch ms or s, or ISO string) to YYYY-MM-DD."""
    s = str(ts)
    if s.isdigit():
        v = int(s)
        if v > 1e12:
            v //= 1000          # milliseconds -> seconds
        if v > 1e10:
            v //= 1000
        import datetime as _dt
        try:
            return (_dt.datetime.fromtimestamp(v, _dt.timezone.utc)
                    .strftime("%Y-%m-%d"))
        except (ValueError, OSError, OverflowError):
            return s[:10]
    return s[:10]


class DhanClient:
    def __init__(self, token: str, client_id: Optional[str] = None,
                 cache_dir: str = "data/cache",
                 min_interval: float = 0.15, timeout: float = 25.0,
                 max_retries: int = 3):
        self.token = token
        self.client_id = client_id or ""
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._instruments: Optional[dict[str, str]] = None
        self._last_raw: str = ""
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ http
    def _post(self, path: str, payload: dict) -> requests.Response:
        headers = {
            "access-token": self.token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        with self._lock:
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
        # per-thread jitter: prevents N workers from firing in sync bursts
        time.sleep(threading.get_ident() % 7 * 0.01)
        last_err = None
        for attempt in range(self.max_retries):
            try:
                r = requests.post(BASE_URL + path, json=payload,
                                  headers=headers, timeout=self.timeout)
                # 429 = "slow down" - DO NOT retry immediately (it makes
                # throttling worse); adapt the throttle instead
                if r.status_code == 429:
                    with self._lock:
                        self.min_interval = min(2.5, self.min_interval * 1.5)
                    raise requests.HTTPError(
                        f"429 rate limited ({path}) - throttled "
                        f"(interval now {self.min_interval:.2f}s)")
                if r.status_code in (500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                # gentle recovery: slowly return toward the base interval
                with self._lock:
                    if self.min_interval > 0.5:
                        self.min_interval = max(0.5, self.min_interval * 0.95)
                return r
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise last_err if last_err else RuntimeError("request failed")

    # ------------------------------------------------------------ instruments
    def get_instruments(self, force_refresh: bool = False) -> dict[str, str]:
        """
        Return {TRADING_SYMBOL: security_id} for all NSE equity instruments.
        Downloads Dhan's compact scrip master (or reads the local cache) and
        caches the parsed map to <cache_dir>/instruments_map.csv (refreshed
        daily). Raises RuntimeError only if every source fails.
        """
        if self._instruments is not None and not force_refresh:
            return self._instruments

        map_path = os.path.join(self.cache_dir, "instruments_map.csv")
        repo_map = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "instruments_map.csv")
        # if we are running from the repo root, data/ sits next to us
        if not os.path.exists(repo_map):
            repo_map = os.path.join(os.getcwd(), "data", "instruments_map.csv")

        # 1) repo-bundled map (checked out from git) - always reliable
        if not force_refresh and os.path.exists(repo_map):
            m = self._read_map_cache(repo_map)
            if m:
                self._instruments = m
                self._write_map_cache(map_path, m)   # warm the fast cache
                return m

        # 2) cached map, fresh (< 1 day old)
        if not force_refresh and os.path.exists(map_path):
            age = time.time() - os.path.getmtime(map_path)
            if age < 24 * 3600:
                m = self._read_map_cache(map_path)
                if m:
                    self._instruments = m
                    return m

        # 3) download the compact master
        raw_path = os.path.join(self.cache_dir, "scrip_master.csv")
        try:
            r = requests.get(MASTER_URL, headers=BROWSER_HEADERS,
                             timeout=self.timeout)
            if r.status_code == 200 and r.text.strip():
                with open(raw_path, "wb") as f:
                    f.write(r.content)
                m = self._parse_master(r.text)
                if m:
                    self._write_map_cache(map_path, m)
                    self._instruments = m
                    return m
        except requests.RequestException:
            pass

        # 4) stale cache as last resort
        if os.path.exists(map_path):
            m = self._read_map_cache(map_path)
            if m:
                self._instruments = m
                return m

        raise RuntimeError("could not fetch Dhan instrument master "
                           "(Cloudflare may block this IP)")

    @staticmethod
    def _parse_master(text: str) -> dict[str, str]:
        """Parse the compact scrip master -> {SYMBOL: security_id}.
        Keeps only NSE equity-series rows (SEM_SERIES == 'EQ'); ETFs and
        bonds are still included here (they are removed later by
        liquid_universe())."""
        out: dict[str, str] = {}
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            exch = (row.get("SEM_EXM_EXCH_ID") or "").strip()
            seg = (row.get("SEM_SEGMENT") or "").strip()
            expiry = (row.get("SEM_EXPIRY_DATE") or "").strip()
            series = (row.get("SEM_SERIES") or "").strip()
            if exch != "NSE" or seg != "E" or expiry or series != "EQ":
                continue
            sym = (row.get("SEM_TRADING_SYMBOL") or "").strip()
            sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
            if sym and sid:
                out[sym] = sid
        return out

    @staticmethod
    def _read_map_cache(path: str) -> dict[str, str]:
        try:
            with open(path, newline="") as f:
                return {r["symbol"]: r["security_id"] for r in csv.DictReader(f)}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _write_map_cache(path: str, m: dict[str, str]) -> None:
        try:
            with open(path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "security_id"])
                for sym, sid in m.items():
                    w.writerow([sym, sid])
        except Exception:  # noqa: BLE001
            pass

    def resolve_symbol(self, symbol: str) -> Optional[str]:
        """Map a (possibly alias) trading symbol to a Dhan security id."""
        sym = symbol.upper().strip()
        sym = SYMBOL_ALIASES.get(sym, sym)
        try:
            m = self.get_instruments()
        except RuntimeError:
            return None
        return m.get(sym)

    def liquid_universe(self) -> list[str]:
        """
        Symbols of real tradable equities - drops ETFs, index funds, bonds,
        SGBs, T-bills and test scrips - what a full-market scan should
        iterate. Real stocks whose names collide with ETF tokens (GOLDIAM,
        JETFREIGHT, ALPHAGEO, BALPHARMA) are protected by the whitelist.
        """
        try:
            m = self.get_instruments()
        except RuntimeError:
            return []

        def is_fund(s: str) -> bool:
            if s in ETF_WHITELIST_REAL:
                return False
            if s in ETF_EXACT_NAMES:
                return True
            return any(t in s for t in ETF_EXCLUDE_TOKENS)

        return sorted(s for s in m if not is_fund(s))

    # ----------------------------------------------------------------- OHLC
    def get_daily(self, symbol: str, from_date: dt.date, to_date: dt.date,
                  force_refresh: bool = False) -> Optional[dict]:
        """
        Daily bars for `symbol` between from_date..to_date (inclusive).
        Returns dict with 'symbol', 'dates', 'open','high','low','close',
        'volume' (oldest -> newest) or None if no data / unknown symbol.
        """
        security_id = self.resolve_symbol(symbol)
        if security_id is None:
            return None

        cache_file = os.path.join(self.cache_dir, f"{symbol}.csv")
        if not force_refresh and os.path.exists(cache_file):
            bars = self._read_cache(cache_file)
            if bars and bars["dates"] and bars["dates"][-1] >= to_date.isoformat():
                bars["symbol"] = symbol  # cache drops the symbol key - the
                # scanner's alerts need it (else "PATTERN SIGNAL — ?")
                return self._slice(bars, from_date, to_date)

        # SINGLE request - the version proven to work (yesterday + today's
        # 12:22 run found 916 signals with this). Do NOT chunk.
        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            "fromDate": from_date.isoformat(),
            "toDate": to_date.isoformat(),
            "dhanClientId": self.client_id,
        }
        r = self._post("/v2/charts/historical", payload)
        bars = self._parse_ohlc(r)
        if bars is None:
            return None
        bars["symbol"] = symbol
        self._write_cache(cache_file, bars)
        return self._slice(bars, from_date, to_date)

    # ------------------------------------------------------------- intraday
    def get_intraday(self, symbol: str, date: dt.date,
                     interval_minutes: int = 15) -> Optional[list]:
        """
        Intraday candles for `symbol` on `date` via POST /v2/charts/intraday.
        Returns list of dicts {ts, open, high, low, close, volume}
        (oldest -> newest) or None if no data / error.
        """
        security_id = self.resolve_symbol(symbol)
        if security_id is None:
            return None
        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": interval_minutes,
            "oi": False,
            "fromDate": date.isoformat(),
            "toDate": date.isoformat(),
            "dhanClientId": self.client_id,
        }
        r = self._post("/v2/charts/intraday", payload)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            data = data.get("data", data)
        rows = []
        if isinstance(data, list):
            for row in data:
                try:
                    if isinstance(row, dict):
                        ts = row.get("timestamp") or row.get("date") or row.get("time")
                        rows.append({
                            "ts": str(ts),
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row.get("volume", 0) or 0),
                        })
                    else:
                        if len(row) < 5:
                            continue
                        rows.append({
                            "ts": str(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]) if len(row) > 5 else 0.0,
                        })
                except (TypeError, ValueError, IndexError):
                    continue
        if not rows:
            return None
        rows.sort(key=lambda r: r["ts"])
        return rows

    def intraday_partial(self, symbol: str, date: dt.date,
                         interval_minutes: int = 15) -> Optional[dict]:
        rows = self.get_intraday(symbol, date, interval_minutes)
        if not rows:
            return None
        return {
            "open": rows[0]["open"],
            "high": max(r["high"] for r in rows),
            "low": min(r["low"] for r in rows),
            "close": rows[-1]["close"],
            "volume": sum(r["volume"] for r in rows),
        }

    # ------------------------------------------------------------- parsing
    def _parse_ohlc(self, r: requests.Response) -> Optional[dict]:
        """Tolerant parser for the columnar JSON shape Dhan returns.
        Stores the raw response body in self._last_raw for diagnostics."""
        try:
            data = r.json()
        except Exception as e:  # noqa: BLE001
            self._last_raw = f"json-parse-error: {e} | {r.text[:200]}"
            return None
        if isinstance(data, dict):
            # capture error bodies (quota / invalid token / etc.)
            if "errorType" in data or "errorMessage" in data:
                self._last_raw = (f"{data.get('errorType')} "
                                  f"{data.get('errorCode')} "
                                  f"{data.get('errorMessage')}")
                return None
            inner = data.get("data") if isinstance(data.get("data"), (dict, list)) else data
        else:
            inner = data

        if isinstance(inner, dict) and "close" in inner and "open" in inner:
            ts = (inner.get("timestamp") or inner.get("date")
                  or inner.get("dates"))
            if not ts:
                return None
            return {
                "dates": [_iso_date(x) for x in ts],
                "open": inner["open"], "high": inner["high"],
                "low": inner["low"], "close": inner["close"],
                "volume": inner.get("volume", [0] * len(inner["close"])),
            }

        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            rows = []
            for row in inner:
                try:
                    rows.append({
                        "dates": _iso_date(row.get("timestamp")
                                           or row.get("date")),
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
        except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
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


def token_from_env() -> tuple[Optional[str], Optional[str]]:
    return (os.environ.get("DHAN_ACCESS_TOKEN"),
            os.environ.get("DHAN_CLIENT_ID"))
