#!/usr/bin/env python3
"""
Build data/market_cap.csv (symbol, mcap_cr) for the whole liquid universe
using Yahoo Finance. Run once (or weekly) where Yahoo is reachable:

    python build_mcap.py

Output: data/market_cap.csv  ->  used by the scanner's prefilter
(mcap > 1000 Cr filter). Symbols Yahoo can't resolve are simply skipped
(they then fall back to being checked by the other prefilter conditions).
"""

from __future__ import annotations

import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from dhan_client import DhanClient

OUT = os.path.join("data", "market_cap.csv")
CR = 1e7  # 1 crore = 10,000,000 INR


def mcap_cr(symbol: str) -> float | None:
    try:
        t = yf.Ticker(f"{symbol}.NS")
        mc = t.info.get("marketCap")
        if mc:
            return mc / CR
    except Exception:  # noqa: BLE001
        pass
    return None


def main() -> int:
    client = DhanClient("x", "x")          # only needs the bundled map
    universe = client.liquid_universe()
    print(f"universe: {len(universe)} symbols")

    rows, fails = [], 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(mcap_cr, s): s for s in universe}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            mc = fut.result()
            done += 1
            if mc and mc >= 1000:          # keep only mcap > 1000 Cr anyway
                rows.append((sym, round(mc, 1)))
            else:
                fails += 1
            if done % 500 == 0:
                el = time.time() - t0
                print(f"  ... {done}/{len(universe)} done, "
                      f"{len(rows)} >1000Cr, {el:.0f}s elapsed")

    rows.sort(key=lambda r: -r[1])
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "mcap_cr"])
        w.writerows(rows)

    print(f"\ndone: {len(rows)} symbols with mcap > 1000 Cr "
          f"({fails} skipped/failed) in {time.time()-t0:.0f}s")
    print(f"wrote {OUT}")
    print("sample:", rows[:5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
