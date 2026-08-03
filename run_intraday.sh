#!/usr/bin/env bash
# ============================================================
# One intraday scan pass (live market alert).
#
# Run this every ~15 min during NSE market hours (09:15-15:30 IST).
# Two ways:
#
#   A) Local cron (no GitHub Actions minutes used):
#      crontab -e  ->  add:
#      */15 9-15 * * 1-5  cd /home/user/pattern_scanner && ./run_intraday.sh >> logs/intraday.log 2>&1
#
#   B) Or use the "Intraday Live Scan" GitHub Actions workflow
#      (.github/workflows/intraday.yml) which does the same in
#      the cloud with the secrets from the repo settings.
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

echo "=== intraday scan $(date '+%F %T %Z') ==="
python3 scanner.py \
    --mode live \
    --intraday \
    --watchlist watchlist.txt \
    --telegram \
    --out "logs/intraday_$(date +%F_%H%M).csv"
echo "=== done ==="
