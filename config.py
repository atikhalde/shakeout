"""
Configuration for the BOS -> flush -> SSL-retest -> reversal scanner.

Every threshold here is a tunable. Defaults are calibrated so the three
hand-verified real examples (Sportking India Jul-24/31, Bajaj Finance
Jul-21/27, SPR Auto Jul-21/27) are detected, and obvious non-setups are not.

The setup being hunted (all in the DAILY timeframe):

  1. BOS .......... price breaks above the previous 26-week high (or, if the
                    26-week high was only *tagged*, breaks the prior swing
                    high on the run-up) -> "break of structure"
  2. FAILURE ...... within a few sessions price suddenly falls >= `flush_min_drop`
                    from the post-BOS peak (the failed tag / bull trap)
  3. SSL RETEST ... the flush comes down to within `ssl_tol_up` % ABOVE (or
                    `ssl_tol_dn` % BELOW, as a wick) the sell-side liquidity
                    level = the deepest low of the ~3 weeks BEFORE the BOS
  4. HOLD ......... every CLOSE after the flush is ABOVE the SSL level
                    ("did not close below")
  5. REVERSAL ..... the LAST completed bar is a strong green candle
                    (bounce >= `bounce_min`, body ratio >= `body_ratio_min`)
  6. BEFORE MOVE .. last close is still BELOW the post-BOS peak
                    (the big momentum move has NOT happened yet)
"""

from dataclasses import dataclass


@dataclass
class ScanConfig:
    # ------------------------------------------------------------------ data
    min_bars: int = 160          # bars needed per symbol (130 for 26W high + margin)
    min_price: float = 30.0      # skip penny/illiquid junk
    min_avg_volume: float = 200_000  # 20-day average volume filter (NSE shares)
    volume_lookback: int = 20

    # ---------------------------------------------------------------- BOS
    # '26w'   -> break of max(high) over last ~26 weeks (130 bars)
    # 'swing' -> break of max(high) over last `bos_lookback_swing` bars
    #            (catches the "tagged the 26W high but broke the swing high"
    #             variant, e.g. Bajaj Finance / SPR Auto)
    # 'both'  -> accept whichever gives the most recent BOS
    bos_style: str = "both"
    bos_lookback_26w: int = 130      # ~26 weeks of trading days
    bos_lookback_swing: int = 45
    bos_oldest: int = 12             # BOS must be no older than this many bars
    bos_newest: int = 2              # ... and at least this many bars before signal day
    bos_break_eps: float = 0.0       # break = high > prev_high * (1 + eps)
    # For the 'swing' style: the post-BOS peak must reach at least this
    # fraction of the 26-week high (otherwise it's just a swing break that
    # never approached the real 26W-high level - e.g. ACI at 94.9%).
    # The 3 verified stocks: SPORTKING 101.6%, BAJFINANCE 99.96%,
    # SPR_AUTO 99.93% - all >= 97%.
    swing_26w_proximity: float = 0.97

    # -------------------------------------------------------------- flush
    # Backtest-informed (117 signals, 150 stocks, 3y):
    #   flush >= 6% keeps the 3 verified stocks (BAJFINANCE 6.7%) while
    #   cutting the weakest; the aggressive >=7% recipe is a tuning option
    flush_min_drop: float = 0.06     # peak -> flush-low drawdown >= 6%
    flush_red_day_min: float = 0.015 # at least one red CLOSE >= 1.5% in the flush
                                     # (close-to-close; e.g. Bajaj's flush had
                                     #  -1.9% and -2.6% closes but no fat red body)
    flush_max_age: int = 3           # flush low must be within N bars of signal day

    # --------------------------------------------------------------- SSL
    ssl_pre_lookback: int = 7        # bars before BOS day used for the SSL level
                                     # (the recent base lows right before the
                                     # run-up; 10+ picks up older dips that
                                     # pollute the SSL - e.g. SPORTKING's Jul-13
                                     # dip at 179 vs the real base at ~194)
    ssl_tol_up: float = 0.015        # flush low may be up to 1.5% ABOVE the SSL
    ssl_tol_dn: float = 0.010        # ... or wick up to 1.0% BELOW (close must hold)

    # --------------------------------------------------------- reversal day
    # Backtest-informed: bounce >= 1.8% keeps SPORTKING (2.5%), BAJFINANCE
    # (3.5%) & SPR_AUTO (1.97% real) while cutting weak reversals
    bounce_min: float = 0.018        # signal day close >= prev close * (1 + 1.8%)
    body_ratio_min: float = 0.25     # (close-open)/(high-low) >= 0.25 (strong green)
    near_ssl_close_min: float = 1.005  # signal close must be >= SSL * this

    # -------------------------------------------------------- SSL-touch alert
    # EARLY-WARNING alert (add-on): fire the moment price dips INTO the SSL
    # zone -- the 'flush' step -- BEFORE the reversal candle confirms. This is
    # distinct from the reversal PATTERN SIGNAL: it is a "watch the level"
    # heads-up, so the user sees price approaching the SSL while it is still
    # deciding whether to reverse.
    #   - detects on the flush day itself (t == the bar whose low enters SSL)
    #   - separate tracker/cooldown so a touch does NOT suppress the later
    #     reversal alert for the same stock (everything stays "intact")
    #   - runs in both the daily EOD and --intraday scans
    ssl_touch_alerts: bool = True
    ssl_touch_tracker_file: str = "ssl_touch_tracker.csv"
    ssl_touch_cooldown_days: int = 15

    # -------------------------------------------------------------- scoring
    # Trade threshold. Backtest: score>=70 -> 25% big-move rate vs 18% at 60.
    # The 3 verified stocks scored 77-87 - all comfortably above 70.
    score_threshold: float = 70.0    # min score to report/alert
    take_profit_pct: float = 6.0     # suggested book-profit (exit discipline)

    # ------------------------------------------------------------ tracking
    tracker_enabled: bool = True     # log every alert to CSV, mark HIT/MISS
    tracker_file: str = "signals_tracker.csv"   # after ~5 sessions

    # ------------------------------------------------------------ cooldown
    # Backtest-optimized: repeat signals within 15 days are WORSE
    # (r5 win 30% vs 63% for first signals). Only the FIRST signal per
    # symbol in a cooldown window is reported.
    cooldown_days: int = 15

    # ---------------------------------------------------------------- live
    # ---- BACKTEST pacing (gentle - heavy volume, avoid 429s) ----
    request_interval: float = 1.5    # seconds between calls. GENTLE pace:
                                     # the backtest fetches 1500+ symbols x 5y,
                                     # so it must stay well under Dhan's
                                     # throttle on foreign runner IPs.
    max_workers: int = 1             # serial for the backtest

    # ---- LIVE SCAN pacing (fast enough for the 16:30 IST window) ----
    # Proven at 10:00 on 05-Aug: 0.5s x 3 workers scanned 800 symbols in
    # ~12 min with only 5 errors. Dhan handles this fine.
    live_request_interval: float = 0.5
    live_max_workers: int = 3
    # hard cap on symbols scanned per run - the full ~2145-symbol liquid
    # universe at 2 req/s takes 18+ min MINIMUM plus Dhan latency from
    # GitHub's US runners (often 60+ min). Cap it so a daily run finishes
    # in ~5-8 min. The mcap prefilter runs first (drops known <1000Cr),
    # then this cap trims to the highest-mcap names when exceeded.
    max_symbols_scan: int = 800

    # --------------------------------------------------------- backtest
    # "Big move" definition (Sportking-style pop): best close within 15
    # sessions gains at least this much vs the next-day-open entry.
    big_move_pct: float = 8.0

    # ------------------------------------------------------ data health
    # If this fraction (or more) of symbols fails to FETCH any data (both
    # Dhan and the yfinance fallback), the run is a DATA OUTAGE: "0 signals"
    # is meaningless because the scanner was blind. The summary/Telegram
    # say so loudly, a ::error:: annotation is emitted for the Actions run
    # page, and main() exits non-zero so the scheduled run turns RED
    # instead of showing a green "no signals today".
    # (2026-08-24/28 outage: every run for 4 days scanned ~800 symbols in
    #  ~60s = everything failed, yet runs showed green + "no signals".)
    data_outage_error_frac: float = 0.5   # >=50% fetch failures = outage
    data_outage_min_symbols: int = 25     # ignore tiny local test scans
    # ---- yfinance fallback pacing --------------------------------------
    # Root cause of the 2026-08-24 silent-data outage: once Dhan fails fast
    # (dead 24h token -> 401), ALL ~800 symbols are dumped onto the
    # yfinance fallback in a free-running multi-worker burst. Yahoo
    # rate-limits (429) the runner IP almost immediately, every yf call
    # then fails, and the scan "succeeds" in ~1 min with ZERO data.
    # The fallback must be globally paced (across all workers) and given
    # one quick retry for transient 429s, exactly like the Dhan client.
    yf_min_interval: float = 0.6          # min seconds between Yahoo calls
    yf_retry_delay: float = 1.2           # one retry after a short pause

    # --------------------------------------------------------- prefilter
    # Panel conditions that narrow the universe BEFORE the pattern scan.
    prefilter_enabled: bool = True
    prefilter_close_min: float = 100.0    # daily close > 100
    prefilter_green_daily: bool = True    # daily close > daily open
    prefilter_rsi_min: float = 60.0       # weekly RSI(14) > 60
    prefilter_macd_min: float = 0.0       # weekly MACD hist (26,12,9) > 0
    prefilter_mcap_min: float = 1000.0    # market cap > 1000 Cr
    mcap_file: str = "data/market_cap.csv"
    api_timeout: float = 20.0
