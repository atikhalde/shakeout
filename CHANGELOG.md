# Changelog

Notable changes to the shakeout scanner/backtest. Entries reference the PR that
introduced them; dates are UTC.

## 2026-08-30

- **#13 — Backtest parity: same data as the scanner (Dhan primary + yfinance fallback).**
  The backtest now uses the scanner's own data chain (Dhan historical daily
  bars primary, paced yfinance fallback per symbol), the scanner's universe
  pipeline (Dhan instrument map → `liquid_universe()` → market-cap ≥ 1000 Cr),
  and confirms every candidate day with `pattern.detect_setup()`. Caches are
  separated per source (`data/cache/dhan` vs `data/cache/yf`), per-symbol
  source tags appear in the log and the FetchReport sheet, and `tests.py`
  gains a parity sweep proving backtest rows match the scanner detector
  day-by-day on all demo symbols.

- **#12 — Fix backtest: persist Yahoo bars so the CI cache is actually used.**
  Fetched yfinance bars are written back to the cache the CI job restores,
  instead of being fetched and dropped.

- **#11 — Fix backtest CI: always write artifacts, fail loud on silent data
  truncation.**
  The backtest workflow uploads artifacts on every run and turns incomplete
  data into a red build instead of a silent pass.

- **#10 — Fix backtest: period presets fetch lookback, data outage fails red,
  workflow aligned to Yahoo-only CLI.**

## 2026-08-29

- **#9 — Keep backtest workflow compatible with Yahoo-only CLI.**
- **#8 — Scanner/backtest shakeout fixes.**
- **#7 — Align backtest filters with scanner and add time period presets.**

## 2026-08-28

- **#6 — Fix 4-day silent data outage behind "no pattern alerts"** (pace the
  yfinance fallback and fail loud when both sources are out).
