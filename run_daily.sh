#!/usr/bin/env bash
# ============================================================
# Daily live scan + Telegram alert.
#
# Add to crontab (runs at 16:30 IST Mon-Fri, after market close):
#   30 16 * * 1-5  cd /home/user/pattern_scanner && ./run_daily.sh >> logs/scanner.log 2>&1
#
# Needs: python3, requests, and a filled .env (see .env.example)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

echo "=== scan $(date '+%F %T %Z') ==="
python3 -m pattern_scanner.scanner \
    --mode live \
    --telegram \
    --out "logs/signals_$(date +%F).csv"
echo "=== done ==="
