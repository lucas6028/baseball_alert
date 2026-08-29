"""Where an alert goes when it fires, and what it says when it gets there.

The alert is sized to a measurement, not to a guess. ``cpbl-alert test
--ruler`` pushes a numbered ruler at the real phone; on the reader's, line 4
came back carrying the truncation ellipsis, and no line had wrapped. So the
budget is :data:`LINE_BUDGET` lines of at most :data:`MAX_COLUMNS` columns,
and the alert spends all of it and not one line more -- content past the
budget is written for nobody, and a line that wraps costs the budget exactly
like a written one. Re-measure and these numbers move; that is the point of
them being numbers.

The product name is *not* in the body. Telegram titles a notification with
the chat name, which for a private chat is the bot's own display name, so
printing 快轉台 again would spend the scarcest line on a word already on
screen. The first body line is the scoreboard instead.

The vocabulary is deliberately plain: 台鋼 rather than 台鋼雄鷹, because the
short name is what makes a line fit.

The bases are a real diamond rather than the word 滿壘, and that was a
measurement too. A diagram only reads as one while it stays aligned, and a
lock screen strips the monospace that used to guarantee it -- so the shape
was pushed at the phone before anything was built on it, and it held. It
survives because every cell is a single character that renders full-width in
a CJK context, so nothing has to be padded into place.

What survives the stripping is punctuation and shape, so every mark here is
load-bearing rather than ornamental:

* ``　`` (ideographic space) is the *major* break: at most one per run of
  text, and it always separates two different kinds of thing. The same
  character also pads the diamond's grid, where it is structure rather than
  punctuation -- it is the only character guaranteed to be exactly one cell
  wide, which is what buys the alignment.
* ``・`` joins facts of the same kind into one phrase, because the inning and
  the out count describe a single moment rather than two fields.
* ``◆◇`` is position carrying the meaning -- no linear run of glyphs can say
  *which* base without a label, which is exactly why the diamond is worth two
  lines when the word was worth two characters.
* ``♥♡`` is the one gauge: filled out of ten, so the bar reads as a percent.

Bold is in-app polish only: a lock-screen preview strips formatting, so the
rhythm has to survive as plain text. It does -- which is why the marks do the
work and ``<b>`` merely reinforces it.

Two places an alert can land, Telegram and Discord, and the text is written
once for both: the alert is composed in Telegram's HTML and
:func:`discord_markdown` rewrites the one tag it uses on the way out. Both
carry the name above the body rather than in it -- Telegram from the chat
name, Discord from the webhook's -- so the four lines stay four lines
either way.

Which channel an alert lands in is a per-league question, because CPBL
tension and 台灣選手上場 are two different subscriptions: someone who wants
every 九局滿壘 may want the MLB alerts somewhere quieter, or shared with
people who only care about that one. So :func:`build_notifier` takes the
league and looks for a channel named for it before falling back to the
common one.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Protocol

import requests

from .leverage import Assessment
from .models import GameState

log = logging.getLogger(__name__)

# No BRAND constant here on purpose: the product name belongs in the bot's
# Telegram display name, which is what titles the notification. See the module
# docstring -- printing it in the body too would cost a line to say it twice.
SCORE_LABEL = "心跳指數"

# The major break within a line, and the join between facts. See the module
# docstring: at most one BREAK per line.
BREAK = "　"       # ideographic space
JOIN = "・"        # katakana middle dot -- the narrow ASCII · reads
                   # cramped between full-width characters

# -- the measurement -------------------------------------------------------
# How many lines a notification shows before it truncates is not a number you
# can look up: iOS shows roughly four on the lock screen, stock Android one,
# Samsung's One UI usually two, and every one of those moves with the OS
# version and the reader's font-size setting. So `cpbl-alert test --ruler`
# measures the actual phone, and these are what it reported: four lines, and
# a ruler line this wide that did not wrap.
RULER_LINES = 8         # probe with more than any phone will show
RULER_WIDTH = 17        # columns of rule between the number and the end mark
LINE_BUDGET = 4
# The ruler line that fit, in half-columns: "N " + the rule + the end mark.
MAX_COLUMNS = 2 + RULER_WIDTH * 2 + 2

_BASE_FILLED, _BASE_EMPTY = "◆", "◇"
_HEART_FILLED, _HEART_EMPTY = "♥", "♡"
HEARTS = 10             # one heart per 10 心跳指數, so the bar reads as a %


def columns(text: str) -> int:
    """Display width in half-columns, counting CJK and ambiguous marks as 2.

    Ambiguous-width characters (``─``, ``♥``, ``●``) render full-width in a
    CJK context, which is the context this ships in, so they are counted wide
    rather than narrow. Counting them narrow would let a line pass this check
    and still wrap on the phone -- the one failure the budget exists to stop.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1
               for ch in text)

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


def headline(state: GameState) -> str:
    """'台鋼 4-5 富邦' -- the score bold, because it is what the eye wants."""
    return (f"{team(state.visiting_team)} "
            f"<b>{state.visiting_score}-{state.home_score}</b> "
            f"{team(state.home_team)}")


def tension_gauge(tension: float) -> str:
    """'♥♥♥♥♥♥♥♥♥♡' -- how much it matters, as a shape rather than a number."""
    filled = min(max(int(round(tension / 100 * HEARTS)), 0), HEARTS)
    return _HEART_FILLED * filled + _HEART_EMPTY * (HEARTS - filled)


def situation(state: GameState) -> str:
    """'九上・一出局' -- one moment, so one joined phrase.

    The bases are not in here: the diamond says that, and saying it twice
    would cost line 1 the width it needs for a long team name.
    """
    return JOIN.join((inning_label(state), outs_label(state.outs)))


def diamond_rows(state: GameState) -> tuple[str, str]:
    """The bases as an actual diamond, two rows of a fixed 4-cell grid.

    Row one is second base, row two is third and first, which is the shape
    every scoreboard uses and the reason position can carry the meaning at
    all -- no linear run of glyphs says *which* base without a label.

    Alignment is the whole trick, and it survives here for two reasons.
    Every cell is one character that renders full-width in a CJK context --
    ``◆``, ``◇`` and the ideographic space alike -- which was confirmed by
    pushing this exact shape at a real phone rather than assumed. And the
    grid is a fixed four cells wide, so whatever is hung off the right of
    each row starts in the same column on both.
    """
    def cell(occupied: bool) -> str:
        return _BASE_FILLED if occupied else _BASE_EMPTY

    pad = BREAK
    top = f"{pad}{cell(state.second)}{pad}{pad}"
    bottom = f"{cell(state.third)}{pad}{cell(state.first)}{pad}"
    return top, bottom


def ruler_text(lines: int = RULER_LINES, width: int = RULER_WIDTH) -> str:
    """A numbered ruler to push at a real phone, for sizing the alert.

    Two readings come out of one message. The last number still legible is
    the line budget. The ``┤`` marks where a full-width line ends, so if one
    is pushed onto a row of its own, the alert is too wide -- and a wrapped
    line costs the budget the same as a written one.
    """
    rule = "─" * width
    return "\n".join(f"{i} {rule}┤" for i in range(1, lines + 1))


def format_alert(state: GameState, assessment: Assessment) -> str:
    """Human-facing alert text (Telegram HTML), sized to the measured budget.

    Ordered so that each line still leaves you better off if it is the last
    one you see -- a grouped or stacked notification shows fewer than a lone
    one, and this is the order in which you would ask the questions anyway:

    1. which game, what the score is, and where in the game we are
    2-3. what the situation looks like, and who it comes down to
    4. how much it matters, as a number and as a bar

    Lines two and three carry two things each. The diamond occupies a fixed
    grid on the left so it stays a diamond, and the matchup rides along on
    the right, where it costs no line of its own -- which is the only reason
    both fit inside the budget.
    """
    top, bottom = diamond_rows(state)
    return "\n".join((
        f"{headline(state)}{BREAK}{situation(state)}",
        f"{top}打者 {state.batter}",
        f"{bottom}投手 {state.pitcher}",
        f"{SCORE_LABEL} <b>{assessment.tension:.0f}</b>"
        f"{BREAK}{tension_gauge(assessment.tension)}",
    ))


class OnStage(Protocol):
    """Who an MLB alert is about. See :class:`cpbl_alert.mlb.Spotlight`.

    Structural rather than imported: ``mlb`` imports this module, and the
    dependency only runs that way -- the notifier knows how to lay out a
    baseball situation and stays ignorant of which league produced it.
    """

    role: str
    name: str
    detail: str


# What line four says, by whose arrival triggered the alert. 登板 rather than
# 上場 for a pitcher because that is the word for taking the mound, and the
# verb is the only place a four-line alert can afford to be specific.
STAGE_LABELS = {
    "batter": "台灣打者上場",
    "pitcher": "台灣投手登板",
    "duel": "台灣內戰",
}


def format_mlb_alert(state: GameState, spot: OnStage) -> str:
    """The MLB alert: same four lines, different reason for sending them.

    Lines one to three are the CPBL alert's, unchanged -- a baseball
    situation reads the same in either league, and the diamond was measured
    once. Only line four differs, because the reason differs: 心跳指數 says
    *how much* this moment matters, and here the answer to that is beside
    the point. You asked to be told when he is up. So line four says that,
    and spends whatever is left on how his day has gone.

    Which of lines two and three he is takes no marking: line four names the
    role, and only one of the two can hold it. The one case where that would
    be ambiguous -- a Taiwanese pitcher facing a Taiwanese batter -- is the
    one case that gets its own label, and its own single notification rather
    than two.
    """
    top, bottom = diamond_rows(state)
    reason = STAGE_LABELS.get(spot.role, STAGE_LABELS["batter"])
    detail = getattr(spot, "detail", "")
    return "\n".join((
        f"{headline(state)}{BREAK}{situation(state)}",
        f"{top}打者 {state.batter}",
        f"{bottom}投手 {state.pitcher}",
        f"<b>{reason}</b>" + (f"{BREAK}{detail}" if detail else ""),
    ))


# -- where it goes ---------------------------------------------------------
class Notifier(Protocol):
    def send(self, text: str) -> bool: ...

    @property
    def key(self) -> tuple: ...

    @property
    def label(self) -> str: ...


_BOLD_RE = re.compile(r"</?b>")


def plain_text(text: str) -> str:
    """The alert with its markup stripped -- what a lock screen shows anyway."""
    return _BOLD_RE.sub("", text)


def discord_markdown(text: str) -> str:
    """Telegram HTML -> Discord markdown.

    The alert uses exactly one tag, ``<b>``, so this is a rewrite rather than
    a parser: ``**`` on both ends. Everything else in the alert -- the
    diamond, the hearts, the ideographic spaces -- is literal text in both
    places, and none of it is markdown to Discord.

    An odd tag would leave ``**`` dangling and swallow the rest of the
    message, so an unbalanced count is stripped instead of rewritten. The
    formatting is polish; the four lines are not.
    """
    if text.count("<b>") != text.count("</b>"):
        return plain_text(text)
    return _BOLD_RE.sub("**", text)


class ConsoleNotifier:
    """Fallback / dry-run sink."""

    key = ("console",)
    label = "console"

    def send(self, text: str) -> bool:
        print("\n" + plain_text(text) + "\n" + "-" * 40)
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

    @property
    def key(self) -> tuple:
        return ("telegram", self.chat_id)

    @property
    def label(self) -> str:
        return f"telegram chat {self.chat_id}"

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


# Discord answers a webhook post with 204 No Content on success, and with 429
# plus a ``retry_after`` when the webhook's five-per-two-seconds bucket is
# empty. A rally can fire two alerts a few seconds apart, so a 429 is worth
# one wait and one retry rather than a dropped alert -- but only one, and
# never a long one: an alert that lands a minute late is about a moment that
# has already passed.
DISCORD_MAX_RETRY_WAIT = 5.0


def _retry_after(resp) -> float:
    """Seconds Discord asked us to wait, from the body or the header."""
    try:
        wait = float((resp.json() or {}).get("retry_after"))
    except (ValueError, TypeError, AttributeError):
        try:
            wait = float(resp.headers.get("Retry-After", 1))
        except (ValueError, TypeError):
            wait = 1.0
    return min(max(wait, 0.0), DISCORD_MAX_RETRY_WAIT)


class DiscordNotifier:
    """Push via a Discord webhook.

    A webhook URL *is* a channel -- Server Settings -> Integrations ->
    Webhooks -> New Webhook picks the channel and hands you the URL. That is
    the whole mechanism behind putting CPBL and MLB in different places:
    two webhooks, one per channel, no routing logic anywhere else.

    The name above the message is the webhook's, which is why the body still
    does not print 快轉台 -- same reasoning as Telegram's chat name, same
    four lines.
    """

    def __init__(self, webhook_url: str, timeout: int = 10) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    @property
    def key(self) -> tuple:
        return ("discord", self.webhook_url)

    @property
    def label(self) -> str:
        # The id, never the token: this goes into logs and onto a terminal,
        # and anyone holding the token can post to the channel.
        parts = [p for p in self.webhook_url.split("/") if p]
        return f"discord webhook {parts[-2] if len(parts) >= 2 else '?'}"

    def _post(self, payload: dict):
        return requests.post(self.webhook_url, json=payload, timeout=self.timeout)

    def send(self, text: str) -> bool:
        payload = {
            "content": discord_markdown(text),
            # A player or team name can never be made to ping a room: nothing
            # in an alert is addressed to anybody.
            "allowed_mentions": {"parse": []},
        }
        try:
            resp = self._post(payload)
            if resp.status_code == 429:
                wait = _retry_after(resp)
                log.warning("discord rate limited; retrying in %.1fs", wait)
                time.sleep(wait)
                resp = self._post(payload)
            if not 200 <= resp.status_code < 300:
                log.error("discord send failed %s: %s",
                          resp.status_code, resp.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.error("discord send error: %s", exc)
            return False


class FanOutNotifier:
    """Every sink gets the alert; one failing does not silence the others.

    Telegram and Discord are not alternatives -- someone can reasonably want
    the push on their phone *and* the line in a channel their friends read.
    So a failure is reported but not raised, and the send is only a failure
    if nothing got through.
    """

    def __init__(self, sinks) -> None:
        self.sinks = list(sinks)

    @property
    def key(self) -> tuple:
        return ("fanout",) + tuple(sink.key for sink in self.sinks)

    @property
    def label(self) -> str:
        return " + ".join(sink.label for sink in self.sinks)

    def send(self, text: str) -> bool:
        delivered = False
        for sink in self.sinks:
            if sink.send(text):
                delivered = True
            else:
                log.error("delivery to %s failed", sink.label)
        return delivered


def channel_for(config: dict, key: str, league: str | None) -> str:
    """The league's own channel if it has one, otherwise the common one.

    ``discord_webhook_mlb`` beats ``discord_webhook`` for the MLB watcher and
    is invisible to the CPBL one. Configure only the plain key and both
    leagues share it, which is the setup most people start with.
    """
    if league:
        specific = str(config.get(f"{key}_{league}") or "").strip()
        if specific:
            return specific
    return str(config.get(key) or "").strip()


def build_notifier(config: dict, league: str | None = None) -> Notifier:
    """The sink for one league: Telegram, Discord, both, or the console.

    ``league`` is one of :data:`cpbl_alert.config.LEAGUES`; ``None`` means
    "whatever is configured for everything", which is what the commands that
    are not watching a particular league use.
    """
    sinks: list[Notifier] = []

    token = str(config.get("telegram_token") or "").strip()
    chat_id = channel_for(config, "telegram_chat_id", league)
    if token and chat_id:
        sinks.append(TelegramNotifier(token, chat_id))

    webhook = channel_for(config, "discord_webhook", league)
    if webhook:
        sinks.append(DiscordNotifier(webhook))

    if not sinks:
        log.warning("no telegram or discord channel configured%s "
                    "-- printing to console instead",
                    f" for {league}" if league else "")
        return ConsoleNotifier()
    return sinks[0] if len(sinks) == 1 else FanOutNotifier(sinks)
