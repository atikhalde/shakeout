"""
Telegram alerting for scanner signals (plain Bot API over requests).

Set in .env or environment:
    TELEGRAM_BOT_TOKEN=<token from @BotFather>
    TELEGRAM_CHAT_ID=<your chat id (or -100xxxx for channels)>

Test it instantly (sends the demo signals to your phone):
    python -m pattern_scanner.scanner --mode demo --telegram
"""

from __future__ import annotations

import logging
import time

import requests

API_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str,
                 timeout: float = 15.0, retries: int = 3):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.retries = retries
        self.url = API_URL.format(token=bot_token)

    # ------------------------------------------------------------------ send
    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        payload = {"chat_id": self.chat_id, "text": text,
                   "parse_mode": parse_mode}
        last_err = None
        for attempt in range(self.retries):
            try:
                r = requests.post(self.url, json=payload, timeout=self.timeout)
                if r.status_code == 200:
                    return True
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                logging.warning("Telegram error: %s", last_err)
                return False
            except requests.RequestException as e:  # noqa: BLE001
                last_err = str(e)
                time.sleep(2.0 * (attempt + 1))
        logging.error("Telegram send failed after %d tries: %s",
                      self.retries, last_err)
        return False

    # ------------------------------------------------------------- formatting
    @staticmethod
    def _d(d) -> str:
        """Render a date value (ISO string or epoch) safely."""
        if d is None:
            return "?"
        s = str(d)
        if s.isdigit():
            import datetime as dt
            v = int(s)
            if v > 1e12:
                v //= 1000
            if v > 1e10:
                v //= 1000
            try:
                return (dt.datetime.fromtimestamp(v, dt.timezone.utc)
                        .strftime("%Y-%m-%d"))
            except (ValueError, OSError, OverflowError):
                return s
        return s[:10]

    @staticmethod
    def format_signal(sig: dict) -> str:
        s = sig
        live = bool(s.get("intraday"))
        header = ("🚨 <b>LIVE PATTERN SIGNAL</b> (market open) — {sym} "
                  if live else "🚨 <b>PATTERN SIGNAL</b> — {sym} ")
        note = ("\n⚠️ <i>Intraday signal — the daily candle is still forming; "
                "confirm by close.</i>" if live else "")

        # score breakdown: "BOS freshness 20/25 · Flush depth 18/20 · ..."
        parts = s.get("score_parts") or {}
        if parts:
            breakdown = "  ·  ".join(
                f"{name} {val:.0f}/{max:.0f}"
                for name, (val, max) in parts.items()
            )
            score_line = (f"📊 <b>Score {s['score']:.0f}/100</b> — what's "
                          f"rewarded:\n{breakdown}")
        else:
            score_line = f"📊 <b>Score {s['score']:.0f}/100</b>"

        msg = (
            header +
            "<i>(daily timeframe)</i>\n"
            "📅 <b>{date}</b>  Close ₹{close:.2f}\n"
            "{score_line}\n"
            "🔓 BOS: {bos} ({style}) — broke ₹{brk:.2f}\n"
            "🏔 Peak: ₹{peak:.2f} ({peak_d})\n"
            "📉 Flush: −{drop:.1f}% → low ₹{low:.2f} ({low_d})\n"
            "💧 SSL zone: ₹{ssl:.2f} — closes held ≥ ₹{minc:.2f} ✅\n"
            "🟢 Reversal: +{bounce:.1f}% (body ratio {body:.2f})\n"
            "⏳ Close ₹{close:.2f} still below peak ₹{peak:.2f} "
            "<i>→ big move not fired yet</i>"
        ).format(
            sym=s["symbol"], score=s["score"], date=TelegramNotifier._d(s["signal_date"]),
            close=s["last_close"], bos=TelegramNotifier._d(s["bos_date"]),
            style=s["bos_style"], brk=s["break_level"],
            peak=s["peak"], peak_d=TelegramNotifier._d(s["peak_date"]),
            drop=s["flush_drop_pct"], low=s["flush_low"],
            low_d=TelegramNotifier._d(s["flush_date"]),
            ssl=s["ssl"], minc=s["min_close_after_ssl"],
            bounce=s["bounce_pct"], body=s["body_ratio"],
            score_line=score_line,
        )
        return msg + note

    @staticmethod
    def format_summary(n_signals: int, scope: str = "NSE universe") -> str:
        if n_signals:
            return (f"🔍 <b>Scan complete</b> ({scope})\n"
                    f"{n_signals} signal{'s' if n_signals != 1 else ''} fired.")
        return (f"🔍 <b>Scan complete</b> ({scope})\n"
                f"No pattern signals today. Setup needs: recent BOS → sudden "
                f"flush to SSL zone (no close below) → strong green reversal "
                f"candle, still below peak.")

    # ----------------------------------------------------------- batch helpers
    def send_signals(self, signals: list[dict], scope: str = "NSE universe") -> int:
        """Send one message per signal + a summary. Returns # messages sent."""
        sent = 0
        for sig in sorted(signals, key=lambda s: -s["score"]):
            if self.send(self.format_signal(sig)):
                sent += 1
            time.sleep(0.3)  # be gentle with Telegram's limits
        if self.send(self.format_summary(len(signals), scope)):
            sent += 1
        return sent
