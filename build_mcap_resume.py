#!/usr/bin/env python3
"""
Resume build of data/market_cap.csv: process symbols missing from the file,
tolerating Yahoo rate limits (pause + retry). Run until it finishes or
Ctrl-C; every success is saved incrementally.

    python build_mcap_resume.py [--max-minutes 25]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from dhan_client import DhanClient

OUT = "data/market_cap.csv"
CR = 1e7


def load_existing() -> dict[str, float]:
    if not os.path.exists(OUT):
        return {}
    out = {}
    with open(OUT, newline="") as f:
        for row in csv.DictReader(f):
            try:
                out[row["symbol"].upper()] = float(row["mcap_cr"])
            except (ValueError, KeyError):
                continue
    return out


def save(existing: dict[str, float]) -> None:
    rows = sorted(existing.items(), key=lambda kv: -kv[1])
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "mcap_cr"])
        w.writerows(rows)


def mcap_cr(sym: str) -> float | None:
    try:
        t = yf.Ticker(f"{sym}.NS")
        mc = t.info.get("marketCap")
        return round(mc / CR, 1) if mc else None
    except YFRateLimitError:
        raise
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=int, default=25)
    args = ap.parse_args()
    deadline = time.time() + args.max_minutes * 60

    existing = load_existing()
    uni = DhanClient("x", "x").liquid_universe()
    missing = [s for s in uni if s not in existing]
    print(f"have {len(existing)} | missing {len(missing)} | "
          f"budget {args.max_minutes} min")

    i = 0
    pause = 30
    while i < len(missing) and time.time() < deadline:
        sym = missing[i]
        try:
            mc = mcap_cr(sym)
            i += 1
            pause = max(15, pause // 2)
            if mc is not None:
                existing[sym] = mc
                if len(existing) % 100 == 0:
                    save(existing)
                    print(f"  ... {len(existing)} total "
                          f"({i}/{len(missing)} processed) "
                          f"{time.time():.0f}", flush=True)
            time.sleep(0.15)
        except YFRateLimitError:
            print(f"  rate-limited at {sym} -> pausing {pause}s "
                  f"({len(existing)} so far)", flush=True)
            save(existing)
            time.sleep(pause)
            pause = min(pause * 2, 300)
        except Exception:  # noqa: BLE001
            i += 1

    save(existing)
    big = sum(1 for v in existing.values() if v >= 1000)
    print(f"\ndone: {len(existing)} total ({big} >= 1000 Cr) "
          f"in {time.time() - deadline + args.max_minutes*60:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
