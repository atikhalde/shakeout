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
├── tests.py              # 36 automated checks
├── run_daily.sh          # local cron script (optional)
├── requirements.txt
├── .env.example          # copy to .env and fill in your tokens
├── .gitignore            # keeps .env / cache / logs out of git
├── LICENSE               # MIT
├── .github/workflows/
│   └── daily.yml         # cloud daily scan + Telegram alerts (GitHub Actions)
└── data/cache/           # live-data cache (created on first live run)
```

## Disclaimer

This is a research tool for pattern discovery. The pattern was validated on
three hand-verified examples only — it is **not** a guarantee of future
moves. Always confirm signals against live charts, volume and fundamentals,
and manage risk (stop below the SSL). Nothing here is financial advice.
