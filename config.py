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

    # -------------------------------------------------------------- flush
    flush_min_drop: float = 0.04     # peak -> flush-low drawdown >= 4%
    flush_red_day_min: float = 0.015 # at least one red CLOSE >= 1.5% in the flush
                                     # (close-to-close; e.g. Bajaj's flush had
                                     #  -1.9% and -2.6% closes but no fat red body)
    flush_max_age: int = 3           # flush low must be within N bars of signal day

    # --------------------------------------------------------------- SSL
    ssl_pre_lookback: int = 10       # bars before BOS day used for the SSL level
                                     # (the last ~2 weeks of lows before the run-up)
    ssl_tol_up: float = 0.015        # flush low may be up to 1.5% ABOVE the SSL
    ssl_tol_dn: float = 0.010        # ... or wick up to 1.0% BELOW (close must hold)

    # --------------------------------------------------------- reversal day
    bounce_min: float = 0.015        # signal day close >= prev close * (1 + 1.5%)
    body_ratio_min: float = 0.25     # (close-open)/(high-low) >= 0.25 (strong green)
    near_ssl_close_min: float = 1.005  # signal close must be >= SSL * this

    # -------------------------------------------------------------- scoring
    score_threshold: float = 55.0    # min score to report

    # ---------------------------------------------------------------- live
    request_interval: float = 0.15   # seconds between Dhan API calls (rate limit)
    max_workers: int = 4
    api_timeout: float = 20.0
