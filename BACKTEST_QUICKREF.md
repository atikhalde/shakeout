# Backtest Quick Reference

## ✅ What Was Fixed

### Problem: Backtest ≠ Scanner
The backtest was missing 2 critical filters that the scanner applies:

| Filter | Scanner | Backtest (Before) | Backtest (After) |
|--------|---------|-------------------|------------------|
| min_price (₹30) | ✅ | ❌ Missing | ✅ Fixed |
| avg_volume (200k) | ✅ | ❌ Missing | ✅ Fixed |

**Result:** Backtest could find "phantom signals" for penny stocks and illiquid stocks that the scanner would reject.

### Solution
Added both missing filters to `backtest.py` (lines 242-252).

---

## 🚀 New Feature: Time Period Presets

### Preset Options

| Preset | Days | Use Case | Runtime (500 symbols) |
|--------|------|----------|----------------------|
| `1m` | 30 | Quick sanity check | ~1-2 min |
| `6m` | 180 | Recent performance | ~3-5 min |
| `1y` | 365 | Annual review | ~4-6 min |
| `2y` | 730 | Medium-term validation | ~5-8 min |
| `5y` | 1825 | Long-term backtest | ~8-12 min |

---

## 📝 Usage Examples

### Basic Usage
```bash
# Quick check (1 month, 300 symbols)
python backtest.py --period 1m --limit 300

# Recent performance (6 months, 500 symbols)
python backtest.py --period 6m --limit 500

# Annual review (1 year, all symbols)
python backtest.py --period 1y --limit 0

# Medium-term (2 years, all symbols)
python backtest.py --period 2y --limit 0

# Long-term (5 years, all symbols)
python backtest.py --period 5y --limit 0
```

### With Custom Settings
```bash
# Lower score threshold (more signals, lower quality)
python backtest.py --period 2y --min-score 60

# Higher score threshold (fewer signals, higher quality)
python backtest.py --period 2y --min-score 75

# Only large-cap stocks (≥5000 Cr market cap)
python backtest.py --period 2y --min-mcap 5000

# Debug a specific symbol
python backtest.py --period 1y --debug-symbol RELIANCE
```

### Local Quick Run (No API Token)
```bash
# Data comes from Yahoo Finance - no token needed, ever
pip install yfinance
python backtest.py --period 2y --limit 100
```

---

## 🎯 Common Workflows

### Workflow 1: Validate Config Changes
```bash
# Before changing config
python backtest.py --period 6m --limit 500 --out before.csv

# Make changes to config.py

# After changing config
python backtest.py --period 6m --limit 500 --out after.csv

# Compare before.csv and after.csv
```

### Workflow 2: Monthly Performance Review
```bash
# Run on the 1st of each month
python backtest.py --period 1m --limit 0 --out monthly_$(date +%Y%m).csv
```

### Workflow 3: Comprehensive Analysis
```bash
# Run all time periods
for period in 1m 6m 1y 2y 5y; do
    python backtest.py --period $period --limit 0 \
        --out backtest_${period}.csv
done

# Compare results across time periods
```

### Workflow 4: Score Threshold Optimization
```bash
# Test different thresholds
for score in 60 65 70 75 80; do
    python backtest.py --period 2y --limit 0 \
        --min-score $score --out backtest_score${score}.csv
done

# Find the optimal threshold (highest big-move % with enough signals)
```

---

## 📊 Understanding Output

### Console Summary
```
BACKTEST RESULT — 47 signals, 13 fetch errors, 342s
==========================================================================
entry = NEXT day open (alert at close, enter next open)

              3 days: n= 47  win= 85.1%  avg= +1.92%  med= +1.65%
              5 days: n= 47  win= 72.3%  avg= +2.14%  med= +1.88%
              7 days: n= 47  win= 76.6%  avg= +2.67%  med= +2.21%
             10 days: n= 47  win= 70.2%  avg= +2.89%  med= +2.45%
             15 days: n= 47  win= 68.1%  avg= +3.12%  med= +2.67%
      best within 15d: n= 47  win= 87.2%  avg= +4.23%  med= +3.89%
     worst within 15d: n= 47  win= 23.4%  avg= -2.15%  med= -1.87%

  💥 BIG MOVE (≥ +8% within 15d): 12/47 signals = 25.5%

  score bucket        n   5d-win   7d-win   5d-avg   big-move%
  score >= 50         47   72.3%   76.6%   +2.14%   25.5%
  score >= 60         47   72.3%   76.6%   +2.14%   25.5%
  score >= 70         47   72.3%   76.6%   +2.14%   25.5%
```

### Key Metrics
- **win%**: Percentage of signals with positive returns
- **avg%**: Average return across all signals
- **med%**: Median return (less sensitive to outliers)
- **BIG MOVE%**: Percentage that gained ≥8% within 15 days (the "home run" rate)

### Output Files
1. **signals_backtest.csv**: Every signal with forward returns (r3, r5, r7, r10, r15, max15, min15)
2. **signals_backtest.xlsx**: Excel workbook with:
   - **Signals sheet**: All signals with score breakdown
   - **Summary sheet**: Win rates, averages, and score buckets

---

## 🔧 Troubleshooting

### Problem: "No signals in the window"
**Causes:**
- Time period too short (pattern is rare: ~1-3 signals/month)
- Score threshold too high
- Market conditions don't match the pattern

**Solutions:**
- Increase time period: `--period 2y` or `--period 5y`
- Lower score threshold: `--min-score 60`
- Increase symbol limit: `--limit 0` (all symbols)

### Problem: "Too many fetch errors"
**Causes:**
- Yahoo rate limiting (429 errors)
- Symbol delisted or renamed on Yahoo
- Network issues

**Solutions:**
- Wait and retry (Yahoo rate limits reset quickly)
- Reduce limit: `--limit 300`
- Renamed listings: add to the `_YF_ALIASES` map in `backtest.py`
  (e.g. `SPR_AUTO` -> `SHRIPISTON`)

### Problem: "Backtest is slow"
**Causes:**
- Large universe (`--limit 0` scans the full static list)
- Long time period (5 years = 1,825 days)
- Paced serial fetching (0.6s between Yahoo calls, to avoid 429s)

**Solutions:**
- Reduce limit: `--limit 500`
- Use shorter period: `--period 1y`
- Run in GitHub Actions (parallel execution)

### Problem: "Results don't match scanner"
**Causes:**
- Using old backtest.py (before fix)
- Different config.py settings
- Slight OHLC differences between Yahoo and Dhan daily bars
  (the backtest uses Yahoo; the live scanner uses Dhan)

**Solutions:**
- Update to latest code (with min_price and avg_volume filters)
- Ensure same config.py for both scanner and backtest
- Compare on the same day (the live scanner's last bar can still be
  forming intraday)

---

## 🎓 Best Practices

### 1. Start Small, Scale Up
```bash
# Quick test first
python backtest.py --period 1m --limit 100

# If it works, scale up
python backtest.py --period 2y --limit 0
```

### 2. Use Incremental Periods
```bash
# Run all periods to check consistency
python backtest.py --period 6m --limit 0
python backtest.py --period 1y --limit 0
python backtest.py --period 2y --limit 0
```

### 3. Compare Score Thresholds
```bash
# Test different thresholds
python backtest.py --period 2y --min-score 60 --out score60.csv
python backtest.py --period 2y --min-score 70 --out score70.csv
python backtest.py --period 2y --min-score 80 --out score80.csv
```

### 4. Validate Across Time Periods
If a strategy works in 1y, 2y, and 5y, it's more robust than if it only works in one period.

### 5. Check Big-Move Rate
The pattern's value is in catching +8% moves. Focus on the **BIG MOVE%** metric, not just win rate.

---

## 📈 Expected Results

Based on historical backtests (score ≥70, liquid stocks):

| Metric | Expected Range |
|--------|----------------|
| Signals per year | 5-15 |
| 5-day win rate | 70-85% |
| 7-day win rate | 75-85% |
| 5-day avg return | +2% to +3% |
| BIG MOVE rate (≥8% in 15d) | 20-30% |

**Note:** Results vary by market conditions. Bull markets produce more signals and higher returns.

---

## 🔗 Related Commands

### Run Live Scanner
```bash
python scanner.py --mode live --limit 300 --telegram
```

### Run Demo Mode (No API)
```bash
python scanner.py --mode demo
```

### Run Tests
```bash
python tests.py
```

### Build Market Cap Data
```bash
python build_mcap.py
```

### View Signal Tracker
```bash
python tracker.py --report
```

---

## 📚 Documentation

- **README.md**: Full scanner documentation
- **BACKTEST_FIXES.md**: Detailed fix explanation
- **DEEP_ANALYSIS.md**: Comprehensive code analysis
- **ANALYSIS-2026-08-28.md**: Forensic debugging example

---

**Last Updated:** 2026-08-29  
**Version:** 2.0 (with period presets and filter fixes)
