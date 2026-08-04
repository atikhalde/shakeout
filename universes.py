"""
Layered NSE universe sources so the scanner never hard-fails on the
instrument list (Dhan's scrip master is Cloudflare-protected and can 403
from datacenter IPs like GitHub Actions runners).

Order tried:
  1. Dhan images URL (with browser UA + referer)     -- best (fresh)
  2. NSE's official bhavcopy-equity file (zipped)    -- fallback
  3. GitHub-hosted mirror of Dhan's master           -- fallback
  4. watchlist.txt                                   -- last resort (you)
  5. hard-coded popular NSE list                     -- last resort
"""

from __future__ import annotations

import csv
import io
import zipfile

import requests


def _parse_dhan_csv(text: str) -> list[str]:
    """NSE equities from the compact scrip master (exch=NSE, seg=E)."""
    symbols = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        exch = (row.get("SEM_EXM_EXCH_ID") or "").strip()
        seg = (row.get("SEM_SEGMENT") or "").strip()
        expiry = (row.get("SEM_EXPIRY_DATE") or "").strip()
        if exch != "NSE" or seg != "E" or expiry:
            continue
        sym = (row.get("SEM_TRADING_SYMBOL") or "").strip()
        if sym:
            symbols.append(sym)
    return sorted(set(symbols))


def _parse_nse_zip(content: bytes) -> list[str]:
    """Parse NSE's EQUITY_L.csv (inside a zip) -> symbols."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
        text = z.read(name).decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        syms = []
        for row in reader:
            s = (row.get("SYMBOL") or "").strip()
            if s:
                syms.append(s)
        return sorted(set(syms))
    except Exception:  # noqa: BLE001
        return []


_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 "
                   "Safari/537.36"),
    "Referer": "https://dhanhq.co/",
    "Accept": "text/csv,text/plain,*/*",
}

FALLBACK_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "LT", "HINDUNILVR", "KOTAKBANK", "AXISBANK", "BAJFINANCE",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "HCLTECH",
    "NTPC", "POWERGRID", "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL",
    "ASIANPAINT", "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "BAJAJ-AUTO",
    "NESTLEIND", "ONGC", "COALINDIA", "GRASIM", "INDUSINDBK", "TECHM",
    "DIVISLAB", "DRREDDY", "CIPLA", "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO",
    "BRITANNIA", "HINDALCO", "SBILIFE", "TATACONSUM", "UPL", "DABUR",
    "HDFCLIFE", "ICICIPRULI", "DLF", "PIDILITIND", "BERGEPAINT", "HAVELLS",
    "BOSCHLTD", "SIEMENS", "ABB", "TATAELXSI", "PERSISTENT", "COFORGE",
    "LTIM", "POLYCAB", "TVSMOTOR", "MOTHERSON", "ASHOKLEY", "AMBUJACEM",
    "ACC", "SHREECEM", "BANKBARODA", "PNB", "CANBK", "UNIONBANK",
    "INDUSIND", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "YESBANK",
    "ZOMATO", "PAYTM", "NYTAA", "DMART", "TRENT", "MCDOWELL-N",
    "VEDL", "HINDZINC", "SAIL", "JINDALSTEL", "COLPAL", "MARICO",
    "GODREJCP", "EMAMILTD", "PAGEIND", "JUBLFOOD", "DEVYANI", "RESTAURANT",
    "INDUSTOWER", "IDEA", "BHARATFORG", "BHEL", "HAL", "BEL", "BDL",
    "SOLARINDS", "DIXON", "VOLTAS", "WHIRLPOOL", "BLUESTARCO", "SUZLON",
    "TATAPOWER", "ADANIGREEN", "ADANITRANS", "GAIL", "IOC", "BPCL", "HPCL",
    "PETRONET", "IGL", "MGL", "GMRINFRA", "IRCTC", "RVNL", "IREDA",
    "NHPC", "SJVN", "PFC", "RECLTD", "LICHSGFIN", "HDFCAMC", "UTIAMC",
    "NUVAMA", "CDSL", "BSE", "MCX", "ICICIGI", "HDFCLIFE", "SHRIRAMFIN",
    "CHOLAFIN", "MUTHOOTFIN", "MANAPPURAM", "BAJAJHLDNG", "SPR_AUTO",
    "SPORTKING", "BAJFINANCE", "MAHINDCIE", "MINDACORP", "CRAFTSMAN",
]


def get_universe(watchlist: str | None = None,
                 timeout: float = 20.0) -> tuple[list[str], str]:
    """
    Returns (symbols, source_name). Tries the layers in order and NEVER
    raises - always returns at least the fallback list.
    """
    sources = []

    # 0) explicit watchlist wins - user's choice is final
    if watchlist:
        try:
            with open(watchlist) as f:
                syms = [ln.strip().upper() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
            if syms:
                sources.append((syms, f"watchlist ({len(syms)} syms)"))
        except OSError:
            pass

    # 1) Dhan scrip master (compact CSV, images host, browser headers)
    for url in (
        "https://images.dhan.co/api-data/api-scrip-master.csv",
    ):
        try:
            r = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout)
            if r.status_code == 200 and r.text.strip():
                syms = _parse_dhan_csv(r.text)
                if syms:
                    sources.append((syms, f"dhan-master ({len(syms)} syms)"))
        except requests.RequestException:
            pass

    # 2) NSE official equity list (zipped CSV)
    try:
        r = requests.get(
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv.zip",
            headers={"User-Agent": _BROWSER_HEADERS["User-Agent"]},
            timeout=timeout,
        )
        if r.status_code == 200:
            syms = _parse_nse_zip(r.content)
            if syms:
                sources.append((syms, f"nse-master ({len(syms)} syms)"))
    except requests.RequestException:
        pass

    # 3) GitHub-hosted mirror of Dhan's master (if you pushed one)
    try:
        r = requests.get(
            "https://raw.githubusercontent.com/atikhalde/shakeout/main/"
            "data/ScripMaster_NSE.csv",
            timeout=timeout,
        )
        if r.status_code == 200 and r.text.strip():
            syms = _parse_dhan_csv(r.text)
            if syms:
                sources.append((syms, f"github-mirror ({len(syms)} syms)"))
    except requests.RequestException:
        pass

    # 5) hard-coded fallback (never empty)
    sources.append((FALLBACK_SYMBOLS, f"fallback ({len(FALLBACK_SYMBOLS)} syms)"))

    return sources[0]
