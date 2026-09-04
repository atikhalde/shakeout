# BOS → Flush → SSL-Retest → Reversal Scanner (NSE / Dhan API)

A daily-timeframe scanner that hunts the exact setup seen in three verified
examples:

| Stock | BOS (break of structure) | Sudden fall | SSL retest (no close below) | Reversal signal | Then |
|---|---|---|---|---|---|
| **SPORTKING** | 24-Jul: H 215.66 > 26W-high 215.49 | 30-Jul: −8.2% to 194.99 | SSL 194.38 (Jul shelf) held | **31-Jul** +2.5% | 03-Aug **+14%** gap to 241.5 |
| **BAJFINANCE** | 21-Jul: swing break, tags 26W-high 1074 | 22–24-Jul: −6.7% to 1001.6 | SSL ~1000.5 held (close 1012.6) | **27-Jul** +3.5% | +8% by 31-Jul, 52W-high 1103 broken |
| **SPR_AUTO** | 21-Jul: swing break of 4417.8, tags 4523 | 22–24-Jul: −8.7% to 4126 | SSL ~4113 held (close 4157) | **27-Jul** +2.0% | 4523 broken 03-Aug (C 4524.4) |

The scanner reports stocks **on the reversal day** — i.e. BEFORE the big
momentum move, which is what you asked for.

## The pattern (encoded in `pattern.py`)

1. **BOS** — a recent day (last 2–12 sessions) breaks the previous 26-week
   high (`bos_style='26w'`) *or* the previous 45-day swing high
   (`bos_style='swing'`, catches the "tagged the 26W high but broke the swing
   high" variant). Default `'both'`.
2. **Sudden fall** — after the post-BOS peak, price drops ≥ 4% low-to-peak,
   with at least one red CLOSE ≥ 1.5% in the flush window (close-to-close,
   because Bajaj's flush had no fat red bodies — only −1.9% / −2.6% closes).
3. **SSL retest** — the flush low reaches within 2% above (or 1% below as a
   wick) the SSL = the deepest low of the ~2 weeks before the BOS day
   (the CHoCH / sell-side liquidity zone).
4. **Hold** — every close from the flush day onward stays **above** the SSL.
5. **Reversal** — the last completed bar is a strong green candle: body
   ratio ≥ 0.25, bounce ≥ 1.5% vs previous close.
6. **Before the big move** — last close is still **below** the post-BOS peak.

Ranking score 0–100 blends BOS freshness, flush depth, SSL precision, bounce
strength, candle quality and trend state (EMA20 > EMA50 bonus).

## Install

```bash
pip install numpy requests
```

## Quick start — demo mode (no API needed)

```bash
python tests.py      # 36 checks: 3 positives + 7 negatives
python scanner.py --mode demo
```

Expected output (all three stocks flagged on their exact reversal dates):

```
#  Symbol      Score  Signal      ...  BOS         Style  Peak    FlushLow  FlushD      Drop%  SSL     MinCl>SSL  Bounce%
1  BAJFINANCE  86     2026-07-27  ...  2026-07-21  swing  1073.6  1001.6    2026-07-24  6.7    1000.0  1012.6     3.5
2  SPORTKING   81     2026-07-31  ...  2026-07-29  swing  219.0   195.0     2026-07-30  11.0   194.6   196.5      2.5
3  SPR_AUTO    78     2026-07-27  ...  2026-07-21  swing  4520.0  4126.0    2026-07-24  8.7    4105.0  4157.0     2.0
```

## Live scan with your Dhan API

> ⚠️ **DhanHQ access tokens are valid ~24 HOURS** (exchange regulation). A
> token pasted into `.env` or a GitHub secret is dead a day later — most
> "no alerts for days" issues come down to this. Rotate it daily, automate
> renewal (`GET /v2/RenewToken`, before expiry), or use the 12-month API
> key + secret flow. With a dead token the scanner automatically runs on
> the **paced yfinance fallback** (see *🛑 Data-outage protection* below).

1. Get your token: Dhan → Settings → API → create app → copy the **access
   token** (and note the **client id** if your setup uses one).
2. Set environment variables (or pass `--token` / `--client-id`):

```bash
export DHAN_ACCESS_TOKEN=your_token_here
export DHAN_CLIENT_ID=your_client_id_here     # optional

# scan the whole NSE equity universe (first 300 symbols for a quick test):
python scanner.py --mode live --limit 300

# scan only your watchlist (one symbol per line):
python scanner.py --mode live --watchlist watchlist.txt

# save results:
python scanner.py --mode live --out signals.csv
```

Notes on the live mode:
- Fetches daily bars for the last 400 calendar days (`--from-days`) via
  `GET /v2/charts/historical` (NSE_EQ, instrumentType=1).
- **Caches** every symbol to `data/cache/<SYMBOL>.csv` — the second run is
  nearly free and you won't hammer the rate limit. Use `--refresh` to force.
- Throttled to ~6–7 requests/sec by default (`request_interval` in
  `config.py`); the full ~2000-symbol universe takes a few minutes.
- If Dhan changes the JSON shape, adjust `_parse_ohlc` in `dhan_client.py`
  (it already handles columnar and row formats).

### 🛑 Data-outage protection (2026-08-28)

A scanner that can't fetch data must not look like a healthy quiet day.
Run health is now verified on every scan:

- If **≥ 50% of symbols fail to fetch** (dead Dhan token AND the yfinance
  fallback failing), the run is a **data outage**: the Actions log gets a
  `::error::` annotation, the run-page summary a `🛑 data: OUTAGE` line,
  Telegram gets **"🛑 SCAN DEGRADED — data outage"** (instead of "No
  pattern signals today"), and `scanner.py` exits **3** so the scheduled
  workflow run turns **red**.
- The yfinance fallback is **paced process-wide** (`yf_min_interval`,
  default 0.6 s across all workers) with one retry — an un-paced burst
  against Yahoo is what turned the dead-token days of 2026-08-24..28 into
  four days of blind, green-looking "0 signals" runs.
- The fallback fetches `end = to_date + 1` (yfinance's `end` is exclusive),
  so fallback-carried runs no longer scan the market as of yesterday.
- Every quiet-day summary now shows `data health: N/M fetched` and a
  `prefilter rejects:` tally, so "why no alerts?" is answerable from the
  run page alone.

## 🔔 Telegram alerts (direct to your phone)

1. **Create the bot**: talk to [@BotFather](https://t.me/BotFather) →
   `/newbot` → choose a name → copy the **token**.
2. **Get your chat id**: send any message to your bot, then open:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` — your id appears as
   `"chat":{"id":123456789}` (for groups/channels use the `-100…` id).
3. **Put both in `.env`** (copy from `.env.example`):

```bash
cp .env.example .env
# then edit .env:
#   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
#   TELEGRAM_CHAT_ID=123456789
#   DHAN_ACCESS_TOKEN=...
#   DHAN_CLIENT_ID=...
```

4. **Test the alert in 10 seconds** (sends the demo signals to your phone):

```bash
python scanner.py --mode demo --telegram
```

5. **Daily automatic alerts** — add to crontab (runs 16:30 IST Mon–Fri,
   right after the market close):

```bash
crontab -e
# add this line:
30 16 * * 1-5  cd /home/user/pattern_scanner && ./run_daily.sh >> logs/scanner.log 2>&1
```

Every signal arrives on Telegram like this:

```
🚨 PATTERN SIGNAL — SPORTKING (score 81/100)
📅 2026-07-31  Close ₹201.44
🔓 BOS: 2026-07-29 (swing) — broke 218.69
🏔 Peak: 219.03 (2026-07-29)
📉 Flush: −11.0% → low ₹194.99 (2026-07-30)
💧 SSL zone: ₹194.60 — closes held ≥ ₹196.50 ✅
🟢 Reversal: +2.5% (body ratio 0.79)
⏳ Close ₹201.44 still < peak ₹219.03 → big move not fired yet
```

> 📌 **SSL-ZONE TOUCH alert (early-warning).** In addition to the reversal
> PATTERN SIGNAL, the scanner also sends an **earlier** `🟦 SSL-ZONE TOUCH`
> message the moment price dips **into** the SSL level — the flush day, i.e.
> *before* the reversal candle confirms. It's deliberately lighter (BOS /
> peak / flush / SSL level only — no trade plan) and is a "watch this level"
> heads-up: the full PATTERN SIGNAL still follows if the close holds above SSL
> and a reversal candle prints. It fires on the flush day, runs in both the
> daily and the `--intraday` scans, and uses a **separate cooldown/tracker**
> (`ssl_touch_tracker.csv`) so a touch never suppresses the later reversal
> alert for the same stock.

`.env` is gitignored, so your tokens are never committed.

## ☁️ Run daily in the cloud — GitHub Actions (no PC needed)

The repo includes `.github/workflows/daily.yml`, which:
- runs **Mon–Fri at 16:30 IST (11:00 UTC)** — right after the NSE close,
  so the daily candle is final (change the `cron` line to adjust)
- pulls the whole NSE universe via Dhan, runs the scan, **sends every
  signal to your Telegram**, and attaches the CSV as a downloadable artifact
- can also be triggered manually from the Actions tab

### Step 1 — Add your 4 secrets (2 minutes)

1. Open your repo → **Settings** tab (top right)
2. Left sidebar → **Secrets and variables** → **Actions**
3. Click **New repository secret** and add these one by one:

| Name | Value |
|---|---|
| `DHAN_ACCESS_TOKEN` | your Dhan access token |
| `DHAN_CLIENT_ID` | your Dhan client id |
| `TELEGRAM_BOT_TOKEN` | your bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | your chat id (e.g. `123456789` or `-100...`) |

4. That's it. Secrets are **encrypted, masked in logs (`***`), and can never
   be read back** — if you need to change one, just add it again with the
   same name (it overwrites). The workflow passes them to the scanner as
   environment variables automatically.

### Step 2 — Push the workflow

```bash
git add .github
git commit -m "Add daily GitHub Actions scan + Telegram alerts"
git push
```

> ⚠️ Scheduled workflows only run when the workflow file is on the
> **default branch** (usually `main`). Also make sure Actions is enabled:
> repo → Settings → Actions → General → "Allow all actions".

### Step 3 — Test everything (2 minutes)

1. Repo → **Actions** tab → **Daily Shakeout Scan** → **Run workflow**
2. Check **☑ test_only = true** → **Run workflow**
3. Within ~1 minute you should get on Telegram:
   `✅ Shakeout scanner is live. Secrets OK — test message from GitHub Actions.`
4. Now run it again with **test_only = false** and **limit = 300** — a real
   scan of 300 symbols. Watch the live console on the run page; signals (if
   any) arrive on Telegram, and `signals-logs` artifact appears for download.
5. Once you're happy, run it with **limit = 0** (whole universe) or just let
   the daily schedule take over.

### Notes & costs
- **Runtime**: full universe ≈ 2,000 symbols at ~0.4 s each ≈ **15–25 min**.
  GitHub free tier gives **2,000 minutes/month** — a daily 20-min run uses
  ~600, plenty of headroom.
- **The scan starts on `dt.date.today()`** — if you run it on a weekend or
  holiday, Dhan simply returns no new bar and nothing fires (harmless).
- View logs any time: Actions → run → "Run live scan + Telegram alerts"
  step console. CSVs are auto-deleted after 30 days.

---

## ⚡ Live market alerts (during trading hours — not after close)

The daily workflow alerts *after* the market closes. To get the alert
**while the market is open**, use the second workflow: **Intraday Live Scan**
(`.github/workflows/intraday.yml`).

**How it works:** every 15 minutes during NSE hours (09:15–15:15 IST), the
scanner fetches today's intraday candles from Dhan, aggregates them into
**today's partial daily candle**, re-runs the whole pattern on
(history + partial), and alerts the instant the reversal conditions are met —
so you get the signal hours before the close, live.

The Telegram message is marked **"🚨 LIVE PATTERN SIGNAL (market open)"** with
a note that the candle is still forming and to confirm by close. The regular
16:30 IST run then re-confirms with the final daily candle.

### Option A — GitHub Actions (cloud, no PC needed)

Push `intraday.yml` (included in the zip) and edit **`watchlist.txt`** with
your symbols (one per line). The shipped default covers the 3 reference
setups + the **top 120 NSE stocks by market cap** (the same names the daily
scan prioritizes) — replace it with your own holdings/candidates any time.
The workflow:

- runs **09:15 IST kickoff + every 15 min 09:30–15:15 IST** Mon–Fri
- scans your **watchlist** (fast, ~1–3 min per run)
- reuses the daily-history cache between runs via `actions/cache`
- sends every live signal to Telegram

⚠️ **Free-tier minutes warning:** a ~100-symbol watchlist × 26 runs/day ≈
600–1700 min/month — keep `watchlist.txt` ≤ ~150 symbols. Do **not** run
full-universe intraday scans in Actions (would exceed the 2000 free minutes).

**Quiet days are now explainable:** every live run appends its scan stats
to the Actions run page (`scanned … · errors … · prefilter-skipped … ·
near-misses …`) and writes a `<csv>.summary.txt` sidecar into `logs/`, so
a quiet day visibly shows a healthy scan instead of a blank page. If the
Dhan token is expired, the summary and the Telegram message say
**"Dhan OFFLINE - yfinance fallback / rotate token"** instead of failing
silently. The intraday daily-bar cache is persisted between runs via
`actions/cache`.

### Option B — Local machine (unlimited, zero GitHub minutes)

Add this to your crontab (runs every 15 min during market hours):

```bash
crontab -e
*/15 9-15 * * 1-5  cd /home/user/pattern_scanner && ./run_intraday.sh >> logs/intraday.log 2>&1
```

`run_intraday.sh` scans `watchlist.txt` live and alerts Telegram. On a local
machine you can even run the full universe every 15–30 min:

```bash
python3 scanner.py --mode live --intraday --telegram                 # whole NSE
python3 scanner.py --mode live --intraday --watchlist watchlist.txt --telegram
```

### Testing the live mode

```bash
# with a real token, during market hours:
python3 scanner.py --mode live --intraday --watchlist watchlist.txt --telegram --limit 3
```
or use the workflow's manual "Run workflow" with **test_only = true**.


## 🔧 Dhan API fixes (important — was returning 146 errors)

The original client used wrong endpoints/params, so every symbol fetch failed
with HTTP 400/404 and the scan silently reported "0 signals, N errors". Fixed
by matching the official `dhanhq` client:

| Before (broken) | After (working) |
|---|---|
| GET `/v2/instruments` (404) | GET `https://images.dhan.co/api-data/api-scrip-master.csv` (compact master, ~25 MB) |
| segment filter `NSE_EQ` (0 rows) | NSE equities = `SEM_EXM_EXCH_ID=='NSE'` + `SEM_SEGMENT=='E'` + no expiry → **9,653 symbols** |
| GET `/v2/charts/historical` with `symbol=` (400) | **POST** `/v2/charts/historical` with `securityId`, `exchangeSegment`, `instrument`, `expiryCode`, `oi`, `fromDate`, `toDate`, `dhanClientId` |
| `instrumentType=1` | `instrument="EQUITY"` + `expiryCode=0` |

Plus:
- **Bundled instrument map** — `data/instruments_map.csv` (9,653 symbols →
  security IDs) is committed to the repo, so GitHub Actions never depends on
  reaching Dhan's Cloudflare-protected master download. Refresh it weekly:
  `python -c "from dhan_client import DhanClient; DhanClient('x').get_instruments(force_refresh=True)"`
- **Liquid-universe filter** — the full-market scan iterates ~3,250 real
  equities (bonds/SGBs/T-bills/test scrips excluded) via
  `client.liquid_universe()`.
- **Rate limit** — 0.25 s between calls (Dhan allows 5 data requests/sec).
- **Symbol alias** — `SPR_AUTO` → `SHRIPISTON` (old exchange symbol) resolves
  automatically.

> How to tell it's healthy: the scan log should now print
> `instrument map: 9653 NSE equities` and `scanned ~3250 symbols, X signals, 0 errors`.


## 🔒 26-week-high proximity guard (false-positive fix)

A **swing**-style BOS (45-day swing break) is only accepted when the post-BOS
peak reaches **≥ 97%** of the 26-week high (`swing_26w_proximity = 0.97`).
This rejects stocks that only broke a local swing high without ever
approaching the real 26-week-high level.

Verified against real data:
| Stock | Peak vs 26W high | Verdict |
|---|---|---|
| SPORTKING | 101.6% | ✅ accepted |
| BAJFINANCE | 99.96% | ✅ accepted |
| SPR_AUTO | 99.93% | ✅ accepted |
| ACI (real) | 94.9% | ❌ rejected (false positive) |

## 🚫 ETF / fund exclusion

Dhan marks ETFs with series `EQ` too, so they are excluded by name token
(NIFTY, BEES, GOLD, SILVER, IETF, ETF, SETF, LIQUID, etc.) with a whitelist
protecting real stocks (GOLDIAM, JETFREIGHT, ALPHAGEO, BALPHARMA). The
full-market universe is now **~2,145 real equities** (was 3,253 incl. funds).

## 📊 Score breakdown in alerts

Every Telegram alert now shows **what the score rewards**:
`BOS freshness 21/25 · Flush depth 20/20 · SSL precision 18/20 ·
Reversal bounce 10/20 · Candle body 7/15 · Trend (EMA20/50) 5/5`


## 📊 Backtest (same data as the scanner: Dhan primary + yfinance fallback)

`backtest.py` replays the scanner's logic over **the SAME data the live
scanner uses** — Dhan historical daily bars (`DHAN_ACCESS_TOKEN`), with the
paced yfinance fallback per symbol when Dhan fails — and measures what
happened AFTER each signal (entry = next day open). The universe, the
market-cap filter and the detector (`pattern.detect_setup`) are the
scanner's own, so backtest signals line up with what the daily scan sees.

```bash
# locally (same token as the scanner):
export DHAN_ACCESS_TOKEN=...   # DhanHQ tokens are valid ~24h - rotate!
python backtest.py --years 2 --limit 500 --min-score 55

# Yahoo-only mode (no token; different bars - NOT scanner-parity):
python backtest.py --source yfinance --years 2 --limit 200

# or in the cloud: repo -> Actions -> "Backtest" -> Run workflow
#   (years / period / limit / min_score are inputs; CSV is uploaded)
```

Output: `signals_backtest.csv` (every signal + r3/r5/r10/r15/max15/min15
forward returns), `signals_backtest.xlsx` (per-symbol data source in the
FetchReport sheet) and a printed summary with a **score>=60/70/80 split** so
you can see whether raising the threshold improves the win rate.

Reference results (90 large/mid-caps, 2 years, score>=55):
```
              3 days: win 86%  avg +1.8%
              5 days: win 71%  avg +2.0%
              7 days: win ~75% avg +2.5%   ← short-term bounce focus
     best within 15d: win 86%  avg +4.0%
  💥 BIG MOVE (≥+8% in 15d): only on the highest-score setups
  score >= 60 -> 5-day win 83% avg +2.6%
```

The default `score_threshold` is now **70** (was 55 → 60) — the backtest shows
score >= 70 gives ~25% big-move rate vs 18% at 60. Alerts also show the
**big-move target** (entry + 8%, the same basis the backtest's `big_move`
flag uses) and the **3–7 day trade horizon**. At 70 the pattern is RARE by
design — expect multi-day stretches with zero alerts; the scan summary now
reports near-misses (setups that matched but scored 55–69) so quiet days are
explainable.

Tips:
- Start with `--limit 300` to see the speed, then `--limit 0` for the
  scanner's whole liquid universe (after the 1000 Cr mcap filter).
- Caches are separated per source (`data/cache/dhan/` and
  `data/cache/yf/`) and restored by the Actions cache, so repeat runs are
  fast and Yahoo bars can never masquerade as Dhan bars.
- `--period 1m/6m/...` presets define the window signals are REPORTED
  from; the pattern's own lookback (~26 weeks) is fetched automatically
  on top, so short windows still have full BOS/SSL history behind them.
- If (nearly) every symbol fails to fetch (Dhan dead AND the yfinance
  fallback blocked), the run is a **data outage**: it aborts early, prints
  a `::error::` annotation and exits non-zero — a red Actions run instead
  of a green, meaningless "0 signals" (same policy as the live scanner).
- **Live scanner only:** Dhan → yfinance failover is INSTANT (no retry
  back-off waits): a rejected token (401/403) marks Dhan dead for the
  rest of the run, a 429 pauses Dhan for a few seconds while the current
  symbol falls back immediately, and timeouts/5xx never sleep between
  attempts — so a failing Dhan call hands the symbol to yfinance within
  ~a second. The local bar cache (`data/cache/`) is still served first
  even when Dhan is down.


## 🎯 Trade manager (implemented after live validation)

Validated against the user's 3 real stocks (BAJFINANCE 27-Jul +9.9%,
SPR_AUTO 27-Jul +4.1%, SPORTKING 31-Jul +14%): a volume-surge filter or a
NIFTY-regime filter would have REJECTED all three - this is a COUNTER-TREND
pattern (it fires right after a flush, often in weak markets). So:

| Implemented | Dropped (would have killed the best trades) |
|---|---|
| ✅ **Score >= 70** default alert threshold (backtest: 25% big-move rate vs 18% at 60) | ❌ Volume-surge as a hard filter (reversal days were 0.6-0.8x, not 1.5x) |
| ✅ **Trade plan in every alert**: Entry = close, Stop = below SSL, Target = entry+8% (big-move pop), R:R = (target−entry)/(entry−stop) — always a valid LONG | ❌ NIFTY-regime gate (NIFTY was below its 20-EMA on all 3 signal days) |
| ✅ **Volume surge shown as INFO** (not a filter) | |
| ✅ **Signal tracking sheet** (`signals_tracker.csv`): every alert logged with entry/stop/target/rr; marked HIT/MISS ~5 sessions later automatically | |
| ✅ **Exit discipline**: pattern is a 3-7 day bounce; book profit around +6% (`take_profit_pct`) | |

Example alert section (target = entry + 8%, so it is always ABOVE the
entry and R:R is always positive):
```
🎯 TRADE PLAN — Entry ₹201.44 · Stop ₹194.80 (−3.3%) · Target ₹217.56 (+8.0%) · R:R 2.43
🔥 Reversal volume 1.96x of 20d avg (info)
```
Track: `python tracker.py --report`

## 📦 GitHub setup

The folder is ready to push as-is:

```bash
cd pattern_scanner
git init
git add .
git commit -m "BOS -> flush -> SSL-retest reversal scanner (NSE/Dhan + Telegram alerts)"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

**Before pushing, double-check** with `git status` that these are ignored
(they should be, via `.gitignore`):
- `.env` (your Dhan + Telegram tokens)
- `data/cache/` (downloaded bar data)
- `__pycache__/`, `logs/`, `results_*.csv`

`git check-ignore -v .env` should print a rule. If you ever committed a
secret by mistake, rotate it immediately — GitHub scans for leaked tokens.

A ready-to-upload zip is included alongside this folder:
`pattern_scanner_github.zip` (recreate with
`git archive --format=zip HEAD -o pattern_scanner_github.zip` after cloning).

## Backtest (find how often it fired)

```bash
python scanner.py --mode demo --backtest --backtest-days 120
python scanner.py --mode live --backtest --backtest-days 90 --limit 200
```

## Workflow suggestion

1. Run the live scan **after market close** (≈ 15:45–16:30 IST).
2. Filter `Score >= 60`; open the charts and confirm the level structure
   yourself (SSL line, BOS origin, prior swing highs) — the scanner's SSL is
   an approximation of the *actual* drawn liquidity levels.
3. Add candidates to your watchlist; re-run next day — the pattern often
   gives 1–3 days of follow-through (Sportking's +14% came 2 sessions later).
4. Risk rule from the three examples: **stop below the SSL** (a close under
   SSL invalidates the setup — e.g. NEG_SSL_BREAK in the test suite).

## Tuning knobs (`config.py`)

| Param | Default | Meaning |
|---|---|---|
| `bos_style` | `both` | `26w` / `swing` / `both` |
| `bos_oldest` / `bos_newest` | 12 / 2 | BOS age window (bars before signal) |
| `flush_min_drop` | 4% | min peak→low drawdown |
| `flush_red_day_min` | 1.5% | min single red close in the flush |
| `flush_max_age` | 3 | flush low must be ≤3 bars old |
| `ssl_pre_lookback` | 10 | bars before BOS defining the SSL |
| `ssl_tol_up` / `ssl_tol_dn` | 2% / 1% | how close the flush must get to SSL |
| `bounce_min` | 1.5% | min reversal-day gain |
| `body_ratio_min` | 0.25 | min green-body strength on reversal day |
| `min_avg_volume` | 200k | liquidity filter |
| `score_threshold` | 55 | min score to report |
| `ssl_touch_alerts` | `True` | emit the early **SSL-ZONE TOUCH** alert (see below) |
| `ssl_touch_tracker_file` | `ssl_touch_tracker.csv` | separate cooldown/tracking for touch alerts |
| `ssl_touch_cooldown_days` | 15 | cooldown window for touch alerts |

## File map

```
pattern_scanner/
├── scanner.py            # CLI (demo / live / backtest)
├── pattern.py            # the setup detector + scoring
├── config.py             # all thresholds
├── indicators.py         # EMA / rolling max / ATR helpers
├── dhan_client.py        # Dhan API v2 wrapper (instruments, daily bars, cache)
├── telegram_notifier.py  # Telegram Bot API alerts
├── env_loader.py         # tiny .env loader (no deps)
├── demo_data.py          # 3 real positives + 7 negatives (reconstructed)
├── tests.py              # 44 automated checks
├── run_daily.sh          # local cron: end-of-day scan
├── run_intraday.sh       # local cron: live scan every 15 min
├── watchlist.txt         # symbols for the intraday scan (edit me)
├── requirements.txt
├── .env.example          # copy to .env and fill in your tokens
├── .gitignore            # keeps .env / cache / logs out of git
├── LICENSE               # MIT
├── .github/workflows/
│   ├── daily.yml         # cloud: end-of-day scan 16:30 IST + Telegram
│   └── intraday.yml      # cloud: LIVE scan every 15 min during market hours
└── data/cache/           # live-data cache (created on first live run)
```

## Disclaimer

This is a research tool for pattern discovery. The pattern was validated on
three hand-verified examples only — it is **not** a guarantee of future
moves. Always confirm signals against live charts, volume and fundamentals,
and manage risk (stop below the SSL). Nothing here is financial advice.
