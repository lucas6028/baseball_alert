"""Where an alert goes when it fires.

The notification leads with the product name because that is the whole
point: on a lock screen, "快轉台" is the message -- the score below it is
just the detail.

Everything after that first line is written in the register of PTT's
Baseball board, which is where this audience already watches games: a
``[LIVE]`` scoreboard headline, a ``※ 發信站`` footer, and the reasoning
delivered as 推文 rather than as bullet points. The vocabulary lives in
:mod:`cpbl_alert.ptt`; this module only decides the running order -- and
that order is deliberately front-loaded, so the two lines a phone preview
shows are still the score and the situation, never the scaffolding.
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from . import ptt
from .leverage import Assessment
from .models import GameState

log = logging.getLogger(__name__)

BRAND = "快轉台"
SCORE_LABEL = "心跳指數"

_BAR_FULL, _BAR_EMPTY = "♥", "♡"


def _bar(tension: float, width: int = 10) -> str:
    filled = int(round(tension / 100 * width))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def _diamond(state: GameState) -> str:
    """Tiny visual of the bases: filled = occupied."""
    second = "◆" if state.second else "◇"
    third = "◆" if state.third else "◇"
    first = "◆" if state.first else "◇"
    return f"　{second}\n{third}　{first}"


def format_alert(state: GameState, assessment: Assessment) -> str:
    """Human-facing alert text (Telegram HTML), written as a 直播文."""
    outs = "●" * state.outs + "○" * (2 - state.outs)
    lines = [
        f"<b>{BRAND}</b>　{ptt.headline(state)}",
        f"<b>{ptt.inning_label(state)}　{ptt.outs_label(state.outs)}</b>　{outs}",
        "",
        f"<code>{_diamond(state)}</code>",
        "",
        f"打者　{state.batter}",
        f"投手　{state.pitcher}",
        "",
        f"{SCORE_LABEL} <b>{assessment.tension:.0f}</b>　"
        f"{ptt.tension_word(assessment.tension)}　{_bar(assessment.tension)}",
        ptt.footer(BRAND),
        *ptt.push_lines(state, assessment),
    ]
    return "\n".join(lines)


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...


class ConsoleNotifier:
    """Fallback / dry-run sink."""

    def send(self, text: str) -> bool:
        import re
        print("\n" + re.sub(r"</?(b|code)>", "", text) + "\n" + "-" * 40)
        return True


class TelegramNotifier:
    """Push via the Telegram Bot API.

    Create a bot with @BotFather to get ``token``; message the bot once, then
    read your ``chat_id`` from https://api.telegram.org/bot<token>/getUpdates
    (``cpbl-alert chat-id`` does this for you).
    """

    def __init__(self, token: str, chat_id: str, timeout: int = 10) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> bool:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                log.error("telegram send failed %s: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.error("telegram send error: %s", exc)
            return False


def build_notifier(config: dict) -> Notifier:
    token = (config.get("telegram_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip()
    if token and chat_id:
        return TelegramNotifier(token, chat_id)
    log.warning("no telegram credentials configured -- printing to console instead")
    return ConsoleNotifier()
