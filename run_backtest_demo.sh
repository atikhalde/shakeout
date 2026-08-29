#!/bin/bash
# Backtest Demo Script - Shows how to use the new --period presets
# Run this script to see backtest examples with different time periods

set -e  # Exit on error

echo "==================================================================="
echo "Backtest Demo - Time Period Presets"
echo "==================================================================="
echo ""
echo "This script demonstrates the new --period presets for backtest.py"
echo ""

# Check if we have a Dhan token
if [ -z "$DHAN_ACCESS_TOKEN" ]; then
    echo "⚠️  No DHAN_ACCESS_TOKEN found in environment."
    echo "   Switching to Yahoo Finance (yfinance) for demo..."
    echo ""
    SOURCE="yfinance"
    LIMIT="100"  # Smaller limit for yfinance (rate limits)
else
    echo "✅ DHAN_ACCESS_TOKEN found - using Dhan API"
    echo ""
    SOURCE="dhan"
    LIMIT="300"  # Larger limit for Dhan
fi

echo "==================================================================="
echo "Demo 1: Quick Backtest (1 Month)"
echo "==================================================================="
echo "Command: python backtest.py --source $SOURCE --period 1m --limit $LIMIT"
echo ""
echo "Use case: Quick sanity check after config changes"
echo "Expected runtime: ~1-2 minutes"
echo ""
read -p "Press Enter to run (or Ctrl+C to skip)..."
echo ""

python backtest.py --source $SOURCE --period 1m --limit $LIMIT --min-score 70

echo ""
echo "==================================================================="
echo "Demo 2: Recent Performance (6 Months)"
echo "==================================================================="
echo "Command: python backtest.py --source $SOURCE --period 6m --limit $LIMIT"
echo ""
echo "Use case: Review recent pattern performance"
echo "Expected runtime: ~3-5 minutes"
echo ""
read -p "Press Enter to run (or Ctrl+C to skip)..."
echo ""

python backtest.py --source $SOURCE --period 6m --limit $LIMIT --min-score 70

echo ""
echo "==================================================================="
echo "Demo 3: Annual Review (1 Year)"
echo "==================================================================="
echo "Command: python backtest.py --source $SOURCE --period 1y --limit $LIMIT"
echo ""
echo "Use case: Annual performance review"
echo "Expected runtime: ~4-6 minutes"
echo ""
read -p "Press Enter to run (or Ctrl+C to skip)..."
echo ""

python backtest.py --source $SOURCE --period 1y --limit $LIMIT --min-score 70

echo ""
echo "==================================================================="
echo "Demo 4: Medium-Term Validation (2 Years)"
echo "==================================================================="
echo "Command: python backtest.py --source $SOURCE --period 2y --limit $LIMIT"
echo ""
echo "Use case: Validate pattern over medium term"
echo "Expected runtime: ~5-8 minutes"
echo ""
read -p "Press Enter to run (or Ctrl+C to skip)..."
echo ""

python backtest.py --source $SOURCE --period 2y --limit $LIMIT --min-score 70

echo ""
echo "==================================================================="
echo "Demo 5: Long-Term Backtest (5 Years)"
echo "==================================================================="
echo "Command: python backtest.py --source $SOURCE --period 5y --limit $LIMIT"
echo ""
echo "Use case: Comprehensive long-term analysis"
echo "Expected runtime: ~8-12 minutes"
echo ""
read -p "Press Enter to run (or Ctrl+C to skip)..."
echo ""

python backtest.py --source $SOURCE --period 5y --limit $LIMIT --min-score 70

echo ""
echo "==================================================================="
echo "Demo Complete!"
echo "==================================================================="
echo ""
echo "Summary of time period presets:"
echo "  1m  = 30 days    (quick sanity check)"
echo "  6m  = 180 days   (recent performance)"
echo "  1y  = 365 days   (annual review)"
echo "  2y  = 730 days   (medium-term validation)"
echo "  5y  = 1825 days  (long-term backtest)"
echo ""
echo "Output files:"
echo "  signals_backtest.csv   - All signals with forward returns"
echo "  signals_backtest.xlsx  - Excel workbook with Signals + Summary sheets"
echo ""
echo "Next steps:"
echo "  1. Review signals_backtest.xlsx for detailed analysis"
echo "  2. Check win rates and big-move percentages"
echo "  3. Compare different time periods to validate consistency"
echo "  4. Adjust score threshold if needed (higher = fewer but better signals)"
echo ""
