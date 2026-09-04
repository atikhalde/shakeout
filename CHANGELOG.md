# Changelog

Notable changes to the shakeout scanner/backtest. Entries reference the PR that
introduced them; dates are UTC.

## 2026-09-04

- **SSL-zone TOUCH alert (early-warning add-on).** The scanner now also alerts
  the moment price dips INTO the sell-side liquidity (SSL) level — the "flush"
  step — *before* the reversal candle confirms. The existing reversal
  PATTERN SIGNAL is unchanged and still fires exactly as before.
  - New `pattern.detect_ssl_touch()` detects the touch on the flush day itself
    (the bar whose low first enters the SSL zone) and shares the
    BOS / peak / flush / SSL anatomy with the reversal detector, without
    requiring the green reversal candle.
  - Distinct, lighter Telegram message (`SSL-ZONE TOUCH`) sent via
    `TelegramNotifier.send_ssl_touches` / `format_ssl_touch` — no trade plan,
    just the BOS / peak / flush / SSL level as a "watch this level" heads-up.
  - **Separate cooldown & tracking** (`ssl_touch_tracker.csv`) so a touch never
    suppresses the later reversal alert for the same stock (and vice versa) —
    everything stays intact.
  - Runs in both the daily EOD and the `--intraday` scans; the daily-green
    prefilter is relaxed for the touch channel (the flush day is normally red)
    while weekly RSI/MACD/close still gate the candidate universe.
  - New config: `ssl_touch_alerts`, `ssl_touch_tracker_file`,
    `ssl_touch_cooldown_days`; a `*.touches.csv` sidecar is written next to the
    signals CSV when touches fire.
  - `tests.py` gains coverage for the detector, the touch Telegram format, and
    the independent touch cooldown.

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
