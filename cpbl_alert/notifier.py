"""Where an alert goes when it fires, and what it says when it gets there.

The notification leads with the product name because that is the whole
point: on a lock screen, "快轉台" is the message -- the score below it is
just the detail.

It is two lines, and that is a ceiling rather than a target. A phone preview
shows about two lines before it truncates, so anything past them is written
for nobody: you either switch the TV over or you don't, and you decide that
from the score, the situation, and how loud the 心跳指數 is. Everything that
does not help make that call -- a bases diagram, the batter, a bar of hearts,
commentary -- costs a glance without changing the answer.

The vocabulary is deliberately plain: 台鋼 rather than 台鋼雄鷹 because the
short name is what makes one line fit, 滿壘 rather than a diagram because it
is the same fact in two characters.
"""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from .leverage import Assessment
from .models import GameState

log = logging.getLogger(__name__)

BRAND = "快轉台"
SCORE_LABEL = "心跳指數"

# Nobody says 統一7-ELEVEn獅 out loud, and the full names do not fit on one
# line. An unknown or renamed side (postseason, all-star) falls through to
# its full name rather than to an empty string.
TEAM_ALIASES: dict[str, str] = {
    "中信兄弟": "兄弟",
    "兄弟象": "兄弟",
    "統一7-ELEVEn獅": "統一",
    "統一獅": "統一",
    "樂天桃猿": "樂天",
    "Lamigo桃猿": "Lamigo",
    "富邦悍將": "富邦",
    "義大犀牛": "義大",
    "味全龍": "味全",
    "台鋼雄鷹": "台鋼",
}

_CN_DIGITS = "零一二三四五六七八九"
_CN_OUTS = ("無人出局", "一出局", "兩出局")

# Keyed by GameState.base_code(). Two or three runners read as bare bases,
# the way a scoreboard says them; a lone runner takes 有人 so the label
# cannot be misread as a count.
_BASES: dict[str, str] = {
    "---": "壘上無人",
    "1--": "一壘有人",
    "-2-": "二壘有人",
    "--3": "三壘有人",
    "12-": "一二壘",
    "1-3": "一三壘",
    "-23": "二三壘",
    "123": "滿壘",
}


def team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def cn_number(n: int) -> str:
    """1 -> 一, 12 -> 十二. Extra innings are innings too."""
    if n < 10:
        return _CN_DIGITS[n]
    tens, ones = divmod(n, 10)
    return ("" if tens == 1 else _CN_DIGITS[tens]) + "十" + (
        _CN_DIGITS[ones] if ones else "")


def inning_label(state: GameState) -> str:
    """'四上' -- how a scoreboard says it, not 第4局上半."""
    return f"{cn_number(state.inning)}{state.half}"


def outs_label(outs: int) -> str:
    return _CN_OUTS[outs] if 0 <= outs < len(_CN_OUTS) else f"{outs}出局"


def bases_label(state: GameState) -> str:
    return _BASES.get(state.base_code(), state.base_code())


def headline(state: GameState) -> str:
    return (f"{team(state.visiting_team)} "
            f"{state.visiting_score}-{state.home_score} "
            f"{team(state.home_team)}")


def format_alert(state: GameState, assessment: Assessment) -> str:
    """Human-facing alert text (Telegram HTML). Two lines, both load-bearing.

    Line one is what a truncated preview is guaranteed to show, so it has to
    stand alone: the brand, who is playing, and where the score stands. Line
    two is why it buzzed -- the situation, then the number that decided it.
    """
    return (
        f"<b>{BRAND}</b>　{headline(state)}\n"
        f"{inning_label(state)} {outs_label(state.outs)} {bases_label(state)}"
        f"　{SCORE_LABEL} <b>{assessment.tension:.0f}</b>"
    )


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...


class ConsoleNotifier:
    """Fallback / dry-run sink."""

    def send(self, text: str) -> bool:
        import re
        print("\n" + re.sub(r"</?b>", "", text) + "\n" + "-" * 40)
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
