"""NPB: buzz when a Taiwanese player is the one on stage.

Same question as :mod:`cpbl_alert.mlb`, same four lines on the phone, same
trigger from :mod:`cpbl_alert.stage` -- and almost nothing else in common,
because Japan gives you none of the three things that made the MLB side easy.

  1. **There is no API.** MLB hands the whole league's live situation over in
     one JSON call; NPB publishes HTML for people to read. So this file
     scrapes, and it pays a request per live game on top of the day's
     day's fixture list -- which is why the default poll here is 30s rather
     than 20s and why a game the page already calls finished is never
     fetched again.

  2. **There is no nationality field.** ``birthCountry`` is what let the MLB
     module treat "who counts" as a lookup and keep its name table as a mere
     backstop. Here the table *is* the answer (:data:`TAIWANESE_NPB`), which
     inverts the failure mode: MLB's risk is a wrong Chinese name on a real
     alert, NPB's risk is no alert at all. Hence ``npb_players`` in the
     config, which is not an escape hatch here so much as the supported way
     to add this year's signing without waiting for a release.

  3. **The names are written in Japanese.** 吳念庭 appears as 呉念庭 and 王彥程
     as 王彦程 -- shinjitai forms of characters the player himself writes the
     traditional way. Matching the string NPB prints against the string a
     Taiwanese reader would type therefore fails on exactly the players this
     tool exists for, so both sides go through :func:`normalize` first, and
     the alert prints the traditional form back.

**On the parsing.** Every assumption about NPB's markup is confined to the
"reading the page" block below, and every rule in it is anchored on the
page's own Japanese labels -- 打者, 投手, 回表, アウト -- rather than on class
names or element ids. A label is content: it is on the page because a reader
needs it, so it survives a redesign that renames every div. That is the more
durable anchor and it is also the checkable one: ``cpbl-alert npb-probe``
fetches a real page, shows what each rule matched and what it did not, and
the fix is an edit to one dict.

That command is not optional politeness. These rules were written without
network access to npb.jp, so they are reasoned from the page's vocabulary
rather than measured against its HTML the way the CPBL and MLB clients were
measured against real payloads. Run the probe once against a live game
before trusting the watcher to be silent for the right reason.
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Protocol

import requests

from .models import GameState
from .notifier import (
    Notifier,
    cn_number,
    format_stage_alert,
    format_starter_alert,
)
from .stage import Spotlight, Stage, Upcoming, arrivals, changed

log = logging.getLogger(__name__)

BASE = "https://npb.jp"

MIN_POLL_SECONDS = 15
# The month page is 220KB and a game only appears on it once it has started,
# so this is both a politeness budget and the worst case for how late the
# first alert of a game can be. Two minutes rather than ten because the whole
# point of this feature is a plate appearance of warning, and rather than
# thirty because the alternative is a quarter of a megabyte every half minute
# for the hours before first pitch. Once every scheduled game is under way it
# is not read again at all.
INDEX_TTL = 120
# How far ahead of first pitch a Taiwanese starter is announced. Separate
# from WARMUP_MINUTES on purpose -- that one paces the poller, this one is a
# promise to the reader.
STARTER_LEAD_MINUTES = 30
# What the poller waits when the day has games it has not seen start yet.
# There is nothing else to watch for at that point, and the only way to see
# one start is to read the index again.
PREGAME_POLL_SECONDS = 120
# Slower than MLB's 20s on purpose: a tick here costs one request per live
# game, against a site rather than an API.
DEFAULT_POLL_SECONDS = 30
IDLE_SLEEP = 300
WARMUP_MINUTES = 30

# NPB plays 14:00 (weekends, holidays) to about 23:00, all of it inside one
# JST date. Outside that there is nothing to poll for.
JST = dt.timezone(dt.timedelta(hours=9))
ACTIVE_HOURS = (13, 24)

# -- who counts ------------------------------------------------------------
# Keyed by the name as NPB writes it, valued with the traditional-character
# name to print. Both sides are matched through :func:`normalize`, so a
# player listed here under either orthography is found under both -- the keys
# are NPB's spelling only because that is the string that will actually come
# off the page, and keeping them that way makes the table checkable against
# the site.
#
# Unlike the MLB table this one is not a backstop, it is the whole detector:
# a player missing from here is a player this tool stays silent about. Adding
# one is ``npb_players`` in the config, which needs no code change.
TAIWANESE_NPB: dict[str, str] = {
    # 現役世代
    "古林睿煬": "古林睿煬",     # 日本ハム
    "宋家豪": "宋家豪",         # 楽天
    "王彦程": "王彥程",         # 楽天
    "呉念庭": "吳念庭",         # 西武
    "王柏融": "王柏融",         # 日本ハム
    "張奕": "張奕",             # オリックス
    "呂彦青": "呂彥青",         # 阪神
    "林安可": "林安可",         # 西武 -- found batting seventh in a real
                              # captured lineup while this table did not
                              # list him, which is the failure mode NPB has
                              # and MLB does not: no nationality field, so a
                              # signing nobody has typed in here is silence.
    # 前の世代
    "陽岱鋼": "陽岱鋼",         # 日本ハム / 巨人
    "陳冠宇": "陳冠宇",         # ロッテ / DeNA
    "郭俊麟": "郭俊麟",         # 西武
    "廖任磊": "廖任磊",         # 巨人 / 西武
    "陳偉殷": "陳偉殷",         # 中日
    "林威助": "林威助",         # 阪神
    "蕭一傑": "蕭一傑",         # 阪神 / ソフトバンク
    "鄭凱文": "鄭凱文",         # 阪神
    "陽耀勲": "陽耀勳",         # ソフトバンク
    "林恩宇": "林恩宇",         # 楽天
    "姜建銘": "姜建銘",         # 巨人
    "許銘傑": "許銘傑",         # 西武
    "郭泰源": "郭泰源",         # 西武
    "郭源治": "郭源治",         # 中日
    "郭李建夫": "郭李建夫",     # 西武
    "呂明賜": "呂明賜",         # 巨人
    "陳大豊": "陳大豐",         # 中日 / 阪神
}

# Shinjitai -> traditional, for the characters that actually turn up in these
# names and in NPB's own vocabulary. Not a general-purpose conversion table
# and not meant to become one: a folding rule that is wrong costs a match, so
# this holds only pairs that are unambiguously the same character.
_SHINJITAI: dict[str, str] = {
    "呉": "吳", "彦": "彥", "勲": "勳", "豊": "豐", "楽": "樂", "広": "廣",
    "沢": "澤", "竜": "龍", "斉": "齊", "桜": "櫻", "恵": "惠", "徳": "德",
    "権": "權", "黄": "黃", "温": "溫", "頼": "賴", "荘": "莊", "会": "會",
    "実": "實", "学": "學", "国": "國", "気": "氣", "単": "單", "双": "雙",
    "万": "萬", "与": "與", "声": "聲", "塁": "壘", "発": "發", "帰": "歸",
    "継": "繼", "済": "濟", "湾": "灣", "県": "縣", "駅": "驛", "図": "圖",
}

# Everything a name might be padded or separated with. NPB pads a two-
# character surname out to width with spaces and separates a registered name
# with a middle dot; neither is part of the name.
_NAME_NOISE = re.compile(r"[\s　・･\.,]")


def normalize(name: str) -> str:
    """The form two spellings of one name agree on.

    Folds the shinjitai a Japanese site prints back to the traditional
    characters a Taiwanese reader writes, and drops the padding and the
    middle dots. 呉念庭, 吳念庭 and ``呉 念庭`` all normalize to 吳念庭.
    """
    folded = "".join(_SHINJITAI.get(ch, ch) for ch in (name or ""))
    return _NAME_NOISE.sub("", folded)


_ROSTER: dict[str, str] = {normalize(k): v for k, v in TAIWANESE_NPB.items()}


def display_name(name: str) -> str:
    """The name to print: traditional characters if we know the player.

    An unknown player falls through to whatever NPB printed, which is right
    -- if the config named him, he still gets an alert, and a Japanese
    rendering of his name is a blemish rather than a wrong name.
    """
    return _ROSTER.get(normalize(name), (name or "").strip())


# -- teams -----------------------------------------------------------------
# Keyed by the letter code NPB puts in its own URLs ("f-l-01" is Fighters at
# Lions). The code is the stable identifier -- a sponsor rename moves the
# printed name and leaves the URL alone, which is exactly what happened when
# 横浜ベイスターズ became 横浜DeNAベイスターズ.
NPB_TEAM_CODES: dict[str, str] = {
    "g": "巨人", "t": "阪神", "c": "廣島", "d": "中日", "s": "養樂多",
    "db": "DeNA",
    "h": "軟銀", "f": "火腿", "m": "羅德", "l": "西武", "e": "樂天",
    "b": "歐力士",
}

# The printed names, for a page that gives a name where the URL is not to
# hand. Both the full name and the short form NPB itself uses in a scoreboard.
NPB_TEAM_NAMES: dict[str, str] = {
    "読売ジャイアンツ": "巨人", "巨人": "巨人",
    "阪神タイガース": "阪神", "阪神": "阪神",
    "広島東洋カープ": "廣島", "広島": "廣島",
    "中日ドラゴンズ": "中日", "中日": "中日",
    "東京ヤクルトスワローズ": "養樂多", "ヤクルト": "養樂多",
    "横浜DeNAベイスターズ": "DeNA", "DeNA": "DeNA",
    "福岡ソフトバンクホークス": "軟銀", "ソフトバンク": "軟銀",
    "北海道日本ハムファイターズ": "火腿", "日本ハム": "火腿",
    "千葉ロッテマリーンズ": "羅德", "ロッテ": "羅德",
    "埼玉西武ライオンズ": "西武", "西武": "西武",
    "東北楽天ゴールデンイーグルス": "樂天", "楽天": "樂天",
    "オリックス・バファローズ": "歐力士", "オリックス": "歐力士",
}


_CODE_BY_TEAM: dict[str, str] = {name: code
                                 for code, name in NPB_TEAM_CODES.items()}


def team_code(name: str) -> str:
    """The letter npb.jp puts in its URLs, from a printed team name.

    The way back from :func:`team_name`, and the only way to tell which of
    the month page's fixtures is the game a link points at: the page lists
    fixtures by name and links them by code.
    """
    return _CODE_BY_TEAM.get(team_name(name), "")


def team_name(code_or_name: str) -> str:
    """Chinese short name from either a URL code or a printed Japanese name."""
    raw = (code_or_name or "").strip()
    if raw.lower() in NPB_TEAM_CODES:
        return NPB_TEAM_CODES[raw.lower()]
    return NPB_TEAM_NAMES.get(raw) or NPB_TEAM_NAMES.get(normalize(raw)) or raw


class NpbError(RuntimeError):
    pass


# -- the feed contract -----------------------------------------------------
@dataclass(frozen=True)
class NpbGame:
    """One NPB game as this package needs it, whoever read the page.

    This is the seam. Everything above it is scraping and everything below it
    is the alert, so a different source -- a different site, a cached file, a
    hand-written test -- only has to produce one of these.

    ``status`` is the one field with a closed vocabulary: ``"pregame"``,
    ``"live"``, ``"final"`` or ``"called"`` (中止 / ノーゲーム). Anything else
    is treated as not live.
    """

    game_id: str                       # "2026/0829/f-l-01" -- date and slug
    status: str = "pregame"
    away_team: str = ""                # already Chinese; see team_name()
    home_team: str = ""
    inning: int = 0
    is_top: bool = True
    outs: int = 0
    balls: int = 0
    strikes: int = 0
    away_score: int = 0
    home_score: int = 0
    first: bool = False
    second: bool = False
    third: bool = False
    batter: str = ""                   # as NPB printed it; inferred, see below
    on_deck: str = ""                  # the man behind him, same inference
    pitcher: str = ""
    batting_order: int | None = None   # the slot ``batter`` is due in
    batter_detail: str = ""            # 打率 .275
    pitcher_detail: str = ""           # 投 87球
    starts_at: dt.datetime | None = None
    # 予告先発, filled only for a game that has not started; see Fixture.
    away_starter: str = ""
    home_starter: str = ""


class NpbFeed(Protocol):
    """Where :class:`TaiwaneseWatcher` gets its games. See :class:`NpbClient`."""

    def games(self, day: str) -> list[NpbGame]: ...


def state_from_npb_game(game: NpbGame) -> GameState | None:
    """One :class:`NpbGame` -> the shared :class:`GameState`.

    ``None`` when there is no situation worth reading: a game that is not
    live, one before first pitch, and the swap between half-innings. The swap
    is the same trap MLB sets -- with three outs showing, the page still
    names a batter and a pitcher, but they belong to different halves, and
    firing on that pairing would put a matchup on the phone that never
    happens.

    ``game_sno`` is 0 rather than an invented number: NPB identifies a game by
    a date and a slug, which does not fit an int, and nothing downstream of
    here reads the field. The watcher keys its own state on ``game_id``.
    """
    if game.status != "live" or not game.inning or game.outs >= 3:
        return None
    return GameState(
        game_sno=0,
        year=game.game_id[:4],
        kind_code="NPB",
        inning=game.inning,
        is_top=game.is_top,
        outs=game.outs,
        first=game.first,
        second=game.second,
        third=game.third,
        balls=game.balls,
        strikes=game.strikes,
        visiting_score=game.away_score,
        home_score=game.home_score,
        batter=display_name(game.batter),
        pitcher=display_name(game.pitcher),
        event_no="",
        created_at="",
        visiting_team=game.away_team,
        home_team=game.home_team,
    )


# -- reading the page ------------------------------------------------------
# Every site-specific assumption in this file lives in this block. Unlike the
# first version of it, these rules were written against real pages: six games
# captured off npb.jp through one live evening, 478 consecutive pairs of
# logged events, and every number quoted below is measured against that
# capture rather than reasoned about.
#
# Three things those pages settled, all of which shape everything here:
#
#   1. **npb.jp never publishes the plate appearance in progress.** 最新経過
#      is a log of *finished* ones -- no row ever appears with an empty 結果 --
#      and ``playbyplay.html`` is the same table. There is no 次打者, no count,
#      no runner display for the man actually standing in the box. So the
#      batter is not read, he is *inferred*: the next man in 最新のオーダー
#      after the last one to finish. Looking one slot further ahead is what
#      gives the on-deck alert of :mod:`cpbl_alert.stage`, and it costs
#      nothing extra -- it is the same table and the same arithmetic. The
#      inference is checkable the only way it can be, by asking whether the
#      man it names is the man who finishes the *next* plate appearance
#      logged: 106 times out of 106 across the capture.
#
#   2. **The page header carries all six of the day's games.** Every score and
#      every inning appears twice over, once for this game and once per game
#      in the carousel above it, so a rule run over the whole page reads
#      whichever game happens to be listed first. Everything below runs over
#      :func:`main_block` instead. (This is also why the old ``試合終了``
#      check was wrong: one finished game in the carousel marked all six.)
#
#   3. **最新経過 runs backwards.** The latest half-inning is the *first* h5
#      on the page. :func:`parse_progress` reverses it once, here, so that
#      nothing downstream has to remember.
#
# The anchors are element ids and the page's own Japanese labels rather than
# class names, and ``cpbl-alert npb-probe`` prints what each of them found on
# a real page, which is the intended way to correct any of this.

_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")

# npb.jp's own game links: /scores/2026/0829/f-l-01/
_GAME_LINK_RE = re.compile(r"/scores/(\d{4})/(\d{4})/([a-z]{1,2}-[a-z]{1,2}-\d{1,2})/")
# The slug is home-visitor-number ("f-h-21" is Hawks at Fighters, played at
# エスコンフィールド), which is the opposite of what the letters suggest to an
# English reader and was confirmed against the venue on three separate games.
# It is only a fallback: the line score names both sides outright.
_SLUG_RE = re.compile(r"^([a-z]{1,2})-([a-z]{1,2})-(\d{1,2})$")

def teams_from_slug(slug: str) -> tuple[str, str]:
    """('軟銀', '火腿') from 'f-h-21' -- visitor first, as the alert prints it.

    The slug names the *home* side first. That reads backwards to anyone who
    expects a fixture to be written away-at-home, and it was confirmed on
    three separate games by the venue: f-h-21 was played at エスコンフィールド,
    which is the Fighters' park, so the ``f`` is the home side.

    Only a fallback -- :func:`parse_linescore` names both sides outright, and
    a scoreboard link that never reaches a game page has nothing else.
    """
    found = _SLUG_RE.match((slug or "").strip().lower())
    if not found:
        return "", ""
    return team_name(found.group(2)), team_name(found.group(1))


_MAIN_ANCHOR = 'id="game_stats"'
_ORDER_ANCHOR = 'id="player-order"'
_PROGRESS_ANCHOR = 'id="progress"'


def strip_tags(html: str) -> str:
    """HTML to the text a reader would see, one field per line where it can be.

    Only ``npb-probe`` and the eye behind it read this now -- every rule below
    works on the markup, because the markup is where the person ids are and an
    id is the one thing on this page that survives a substitution.
    """
    text = re.sub(r"(?i)<\s*(br|/tr|/td|/th|/div|/p|/li|/h[1-6])\b[^>]*>",
                  "\n", html or "")
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _text(fragment: str) -> str:
    """One cell's markup as its visible text."""
    return _WS_RE.sub(" ", html_mod.unescape(
        _TAG_RE.sub(" ", fragment or ""))).replace(" ", " ").strip()


def main_block(html: str) -> str:
    """This game's markup, with the six-game header carousel cut away.

    The single most important line in the file: every score, inning and status
    below is read from what this returns, and the carousel above it holds five
    other games' worth of exactly those.
    """
    start = (html or "").find(_MAIN_ANCHOR)
    return html[start:] if start >= 0 else (html or "")


# -- status ----------------------------------------------------------------
# The page states where a game is in a banner of its own: 【試合中 7回表】,
# 【試合終了】, 【中止】. Measured against the capture, that banner is the only
# place any of it is said in words.
_BANNER_RE = re.compile(r"【([^】]{2,24})】")
_LIVE_RE = re.compile(r"試合中\s*(\d{1,2})\s*回\s*(表|裏)")

STATUS_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("called", ("中止", "ノーゲーム", "サスペンデッド")),
    ("final", ("試合終了", "ゲームセット")),
    ("live", ("試合中",)),
    ("pregame", ("試合前", "試合開始前", "先発予告")),
)


def parse_status(block: str) -> tuple[str, int, bool]:
    """``('live', 7, True)`` -- status, inning and whether it is the top half.

    Other banners share the page (the league's name, a team's name over the
    battery), so the first one carrying a status word wins and the rest are
    passed over. A live banner names the inning too; nothing else does.
    """
    for banner in _BANNER_RE.findall(block):
        for status, tokens in STATUS_TOKENS:
            if any(token in banner for token in tokens):
                live = _LIVE_RE.search(banner)
                if live:
                    return status, int(live.group(1)), live.group(2) == "表"
                return status, 0, True
    return "pregame", 0, True


# -- the line score --------------------------------------------------------
_LINE_ROW_RE = re.compile(r'<tr class="(top|bottom)">(.*?)</tr>', re.S)
_FLAG_RE = re.compile(r"flag_([a-z]{1,2})_")
_TOTAL_RE = re.compile(r'<td class="total-1">\s*(\d*)\s*</td>')


def parse_linescore(block: str) -> tuple[str, str, int, int]:
    """``(away code, home code, away runs, home runs)`` off the line score.

    ``tr.top`` is the visiting side and ``tr.bottom`` the home side, the order
    every box score prints. Each row carries its team's code in a
    ``flag_<code>_<year>`` class, and the code is the durable identifier: a
    sponsor rename moves the printed name and leaves it alone, which is
    exactly what happened when 横浜ベイスターズ became 横浜DeNAベイスターズ.
    """
    sides: dict[str, tuple[str, int]] = {}
    for which, row in _LINE_ROW_RE.findall(block):
        code = _FLAG_RE.search(row)
        total = _TOTAL_RE.search(row)
        runs = total.group(1) if total else ""
        sides[which] = (code.group(1) if code else "", int(runs) if runs else 0)
    away = sides.get("top", ("", 0))
    home = sides.get("bottom", ("", 0))
    return away[0], home[0], away[1], home[1]


# -- the batting order -----------------------------------------------------
@dataclass(frozen=True)
class Player:
    """One man as npb.jp identifies him: a person id and a printed name."""

    pid: str = ""
    name: str = ""


@dataclass
class Lineup:
    """One side's 最新のオーダー: nine slots and whoever is pitching."""

    slots: dict[int, Player] = field(default_factory=dict)
    pitcher: Player | None = None

    def slot_of(self, player: Player | None) -> int | None:
        """Where this man bats, by person id and then by name."""
        if not player:
            return None
        for slot, other in self.slots.items():
            if player.pid and other.pid == player.pid:
                return slot
        for slot, other in self.slots.items():
            if player.name and normalize(other.name) == normalize(player.name):
                return slot
        return None


_ORDER_HALF_RE = re.compile(r'class="half_(left|right)"(.*?)</table>', re.S)
_ORDER_ROW_RE = re.compile(r"<tr>\s*<th>(.*?)</th>\s*<th>(.*?)</th>\s*<td>(.*?)</td>",
                           re.S)
_PLAYER_LINK_RE = re.compile(r"/bis/players/(\d+)\.html")


def _player(cell: str) -> Player | None:
    """One order or progress cell as a :class:`Player`, or ``None`` if empty.

    A baserunning event's batter cell is empty -- that row is not a plate
    appearance and must not move the order on -- which is what the ``None``
    is for.
    """
    name = _text(cell)
    if not name:
        return None
    found = _PLAYER_LINK_RE.search(cell)
    return Player(pid=found.group(1) if found else "", name=name)


def parse_order(block: str) -> tuple[Lineup, Lineup]:
    """``(visiting lineup, home lineup)`` from 最新のオーダー.

    ``half_left`` is the visiting side and ``half_right`` the home side, the
    same order the line score uses. It is the *latest* order, which is what
    makes the inference survive a substitution: a pinch hitter is already in
    the table by the time his plate appearance is logged, and the man he
    replaced is not.
    """
    start = block.find(_ORDER_ANCHOR)
    region = block[start:] if start >= 0 else ""
    lineups = {"left": Lineup(), "right": Lineup()}
    for which, table in _ORDER_HALF_RE.findall(region):
        lineup = lineups[which]
        for slot_raw, position_raw, cell in _ORDER_ROW_RE.findall(table):
            player = _player(cell)
            if not player:
                continue
            slot, position = _text(slot_raw), _text(position_raw)
            # 投 names the pitcher in both leagues, and it has to be checked
            # before the slot rather than instead of it: in the Pacific League
            # a designated hitter bats and the pitcher gets an unnumbered row
            # of his own, while in the Central he bats ninth and holds a slot
            # like anyone else.
            if "投" in position:
                lineup.pitcher = player
            if slot.isdigit() and 1 <= int(slot) <= 9:
                lineup.slots[int(slot)] = player
    return lineups["left"], lineups["right"]


# -- the log of what has already happened ----------------------------------
@dataclass(frozen=True)
class Play:
    """One logged event, and the situation it *began* in.

    That is the column order the page uses and it is the whole reason the
    situation has to be derived: 0アウト beside a strikeout means there were
    none out when the man stepped in, not when he walked back.
    """

    outs: int = 0
    bases: tuple[bool, bool, bool] = (False, False, False)
    batter: Player | None = None      # None for a baserunning event
    result: str = ""


@dataclass
class Half:
    """One half-inning of :class:`Play`, in the order they happened."""

    inning: int = 0
    is_top: bool = True
    plays: list[Play] = field(default_factory=list)


_HALF_RE = re.compile(
    r"<h5[^>]*>\s*(\d{1,2})\s*回\s*(表|裏)（.*?の攻撃）\s*</h5>(.*?)(?=<h5|\Z)", re.S)
_PLAY_ROW_RE = re.compile(
    r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>"
    r"\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.S)
_OUTS_RE = re.compile(r"(\d)\s*アウト")


def bases_cell(text: str) -> tuple[bool, bool, bool]:
    """'1・2塁' -> ``(True, True, False)``. An empty cell is nobody on."""
    parts = {piece.strip() for piece in text.replace("塁", "").split("・")}
    return ("1" in parts, "2" in parts, "3" in parts)


def parse_progress(block: str) -> list[Half]:
    """最新経過, oldest half first.

    The page lists the latest half-inning *first*; this reverses it once so
    that ``halves[-1]`` is the current one everywhere else. The
    ``（先発投手）`` line and any other announcement is a ``colspan`` row with
    fewer than five cells, so it never matches and never moves the order on.
    """
    start = block.find(_PROGRESS_ANCHOR)
    if start < 0:
        return []
    end = block.find(_ORDER_ANCHOR, start)
    region = block[start:end if end > start else len(block)]

    halves: list[Half] = []
    for inning, half, body in _HALF_RE.findall(region):
        plays = []
        for outs_raw, bases_raw, batter_raw, _count, result_raw in \
                _PLAY_ROW_RE.findall(body):
            outs = _OUTS_RE.search(_text(outs_raw))
            plays.append(Play(
                outs=int(outs.group(1)) if outs else 0,
                bases=bases_cell(_text(bases_raw)),
                batter=_player(batter_raw),
                result=_text(result_raw),
            ))
        halves.append(Half(inning=int(inning), is_top=half == "表", plays=plays))
    halves.reverse()
    return halves


# -- from the last finished event to the situation now ---------------------
# The page hands over the situation a plate appearance *started* in, so the
# one it is in now has to be carried forward across the play that just ended.
#
# The out count carries exactly. Against the capture -- 478 consecutive pairs
# of rows, where the second row states the truth the first has to predict --
# these rules were right 478 times and wrong none, with nothing unrecognised.
_OUT_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("三重殺",), 3),
    (("併殺", "ゲッツー"), 2),
    (("振り逃げ",), 0),
    (("三振",), 1),
    (("失策", "エラー", "野選", "フィルダースチョイス"), 0),
    (("ヒット", "安打", "ツーベース", "スリーベース", "ホームラン", "本塁打"), 0),
    (("フォアボール", "デッドボール", "四球", "死球", "敬遠"), 0),
    (("盗塁成功", "進塁", "暴投", "ボーク", "ワイルドピッチ"), 0),
    (("盗塁失敗", "牽制死", "走塁死", "タッチアウト"), 1),
    (("フライ", "ライナー", "ゴロ", "犠打", "犠飛", "バント", "アウト"), 1),
)


def outs_made(result: str) -> int | None:
    """How many outs a logged result made. ``None`` if the words are new.

    Order is load-bearing: ショートゴロ併殺打 is two outs and contains ゴロ,
    振り逃げ is none and contains 三振.
    """
    for words, made in _OUT_RULES:
        if any(word in result for word in words):
            return made
    return None


def bases_after(before: tuple[bool, bool, bool],
                result: str) -> tuple[bool, bool, bool] | None:
    """Where the runners are once the play is over. ``None`` if it depends.

    Unlike the out count this cannot be exact, because a scorecard line does
    not say how far a runner went: a single with men on first and third
    leaves either first and second or first and third, and the page does not
    tell you which until the *next* row. So the certain cases are handled as
    certain -- a walk forces, a home run clears, a ball caught in the air or a
    strikeout moves nobody, and any ball in play with nobody on leaves nobody
    on -- and the rest take the convention every scoreboard assumes, that a
    runner takes two bases on a single and scores from second.

    Measured over the capture's 71 distinct transitions: right on 64, wrong on
    one (a single that sent a man first to third), and no rule at all for six
    (a ground ball with men on, where the force is the whole question). The
    caller keeps the last published bases when this returns ``None``, which is
    a stale diamond rather than an invented one. Both are context: the alert
    is about who is up, and that part is not guessed.
    """
    first, second, third = before
    empty = not (first or second or third)
    if "ホームラン" in result or "本塁打" in result:
        return (False, False, False)
    if any(w in result for w in ("フォアボール", "デッドボール", "四球", "死球", "敬遠")):
        if not first:
            return (True, second, third)
        if not second:
            return (True, True, third)
        return (True, True, True)
    if "三振" in result and "振り逃げ" not in result:
        return before
    if any(w in result for w in ("フライ", "ライナー")) and "犠" not in result:
        return before
    if empty:
        if "スリーベース" in result:
            return (False, False, True)
        if "ツーベース" in result:
            return (False, True, False)
        if "ヒット" in result or "安打" in result:
            return (True, False, False)
        if any(w in result for w in ("ゴロ", "バント")):
            return before
        return None
    if "盗塁成功" in result:
        # The base he took is named, so the base he came from is too.
        if "二塁盗塁成功" in result and first:
            return (False, True, third)
        if "三塁盗塁成功" in result and second:
            return (first, False, True)
        return None
    if "スリーベース" in result:
        return (False, False, True)
    if "ツーベース" in result:
        return (False, True, first)
    if "ヒット" in result or "安打" in result:
        return (True, first, False)
    return None


def next_slot(slot: int, ahead: int = 1) -> int:
    """The slot ``ahead`` places on, wrapping nine back round to one."""
    return (slot - 1 + ahead) % 9 + 1


def last_batter(halves: list[Half], is_top: bool) -> Player | None:
    """The last man on this side to finish a plate appearance.

    Walked back through the innings rather than taken from the current one,
    because a half-inning that has only just started has no plays in it yet
    and the order still carries on from where that side left off. Baserunning
    events are skipped: they have no batter, and they do not move the order.
    """
    for half in reversed(halves):
        if half.is_top != is_top:
            continue
        for play in reversed(half.plays):
            if play.batter:
                return play.batter
    return None


REGULATION_INNINGS = 9
REGULATION_OUTS = 3


def next_half(inning: int, is_top: bool,
              away_score: int, home_score: int) -> tuple[int, bool] | None:
    """The half-inning that follows this one, or ``None`` if the game is over.

    Only two endings have to be recognised, and both are about the ninth: the
    home side does not bat when it is already ahead, and nobody bats a tenth
    when the ninth settled it. Everything earlier always has a next half.
    """
    if is_top:
        if inning >= REGULATION_INNINGS and home_score > away_score:
            return None
        return inning, False
    if inning >= REGULATION_INNINGS and away_score != home_score:
        return None
    return inning + 1, True


def parse_game_page(html: str, game_id: str) -> NpbGame:
    """One game page -> one :class:`NpbGame`.

    Pure: it takes a string and returns a dataclass, which is what makes the
    whole feed testable without a network and what lets ``npb-probe`` show its
    work.
    """
    block = main_block(html)
    slug = game_id.rsplit("/", 1)[-1]
    status, banner_inning, banner_top = parse_status(block)
    away_code, home_code, away_score, home_score = parse_linescore(block)
    slug_away, slug_home = teams_from_slug(slug)
    game = NpbGame(
        game_id=game_id,
        status=status,
        away_team=team_name(away_code) or slug_away,
        home_team=team_name(home_code) or slug_home,
        away_score=away_score,
        home_score=home_score,
        inning=banner_inning,
        is_top=banner_top,
    )
    if status != "live":
        return game

    halves = parse_progress(block)
    away_lineup, home_lineup = parse_order(block)
    # The banner lags -- it can still say 2回表 with the side already retired --
    # so 最新経過's own heading is the half to believe. The page keeps the last
    # two of them, and it opens a new one only once the first plate appearance
    # in it is over, which is why the roll-forward below exists.
    current = halves[-1] if halves else None
    inning = current.inning if current else (banner_inning or 1)
    is_top = current.is_top if current else banner_top

    outs, bases = 0, (False, False, False)
    plays = current.plays if current else []
    if plays:
        last = plays[-1]
        made = outs_made(last.result)
        if made is None:
            # Loud, not quiet. An unknown result leaves the out count low,
            # which reads as "still batting" -- and this file's one
            # unaffordable failure is being wrong towards silence.
            log.warning("%s: no out rule for %r -- add one to _OUT_RULES",
                        game_id, last.result)
            made = 0
        outs = last.outs + made
        bases = bases_after(last.bases, last.result) or last.bases

    if outs >= REGULATION_OUTS:
        # The side has been retired and the page has not opened the next
        # half's section yet. Rolling forward here is what keeps the man who
        # leads off from being the one batter this never announces -- and it
        # is not a guess: on the other side of a change of innings there is
        # nobody on, nobody out, and the order carries on from where that
        # side left off, which is still on the page.
        following = next_half(inning, is_top, away_score, home_score)
        if following is None:
            return replace(game, inning=inning, is_top=is_top, outs=outs)
        inning, is_top = following
        outs, bases = 0, (False, False, False)

    offense = away_lineup if is_top else home_lineup
    defense = home_lineup if is_top else away_lineup

    previous = last_batter(halves, is_top)
    slot: int | None
    if previous is not None:
        slot = offense.slot_of(previous)
        if slot is None:
            # He batted but is not in the order any more, so it cannot be
            # carried forward and this game goes quiet until the next event.
            # Say so: silence with no explanation is how this feature fails.
            log.warning("%s: %s finished a plate appearance but is not in the "
                        "order; cannot tell who is up", game_id, previous.name)
    elif inning <= 1:
        slot = 0                       # nobody has batted yet; the leadoff man is up
    else:
        # 最新経過 keeps only the last two half-innings, so a side whose last
        # turn has scrolled off cannot be placed. It comes back by itself as
        # soon as one of its men finishes a plate appearance.
        slot = None
        log.info("%s: no logged plate appearance for the side now batting; "
                 "waiting for one", game_id)

    at_plate = offense.slots.get(next_slot(slot, 1)) if slot is not None else None
    on_deck = offense.slots.get(next_slot(slot, 2)) if slot is not None else None
    pitcher = defense.pitcher
    return replace(
        game,
        inning=inning,
        is_top=is_top,
        outs=outs,
        first=bases[0], second=bases[1], third=bases[2],
        batter=at_plate.name if at_plate else "",
        on_deck=on_deck.name if on_deck else "",
        pitcher=pitcher.name if pitcher else "",
        batting_order=next_slot(slot, 1) if slot is not None else None,
    )


# -- the month page's own fixture list -------------------------------------
# Two different things on that page carry games, and they are not the same
# thing. The **day rows** list every fixture of the month -- both clubs, the
# ballpark, the start time and, on the day itself, the probable starters --
# and carry no links at all. The **header strip** is the six-game carousel
# that sits on every page of npb.jp, and it links a game once it is under
# way. So the rows are how a game is known before it starts and the strip is
# how it is found once it has, and the two are paired by team code.
@dataclass(frozen=True)
class Fixture:
    """One game as the month page lists it, before it has a page of its own."""

    away_team: str = ""                # already Chinese; see team_name()
    home_team: str = ""
    starts_at: dt.datetime | None = None
    away_starter: str = ""             # 予告先発, as npb.jp writes him
    home_starter: str = ""
    slug: str = ""                     # 'f-h' -- home code first, as the URL

    def under_way(self, game_ids: list[str]) -> bool:
        """Has this fixture turned up in the header strip with a link?"""
        return bool(self.slug) and any(
            game_id.rsplit("/", 1)[-1].startswith(f"{self.slug}-")
            for game_id in game_ids)


_FIXTURE_ROW_RE = re.compile(r'<tr id="date{mmdd}"[^>]*>(.*?)</tr>', re.S)
_SIDE_RE = re.compile(r'<div class="team([12])">(.*?)</div>', re.S)
_TIME_RE = re.compile(r'<div class="time">\s*(\d{1,2}):(\d{2})')
# 予告先発, one div per side and in the same order the clubs are printed --
# which is the home side first, the same way round as the URL slug.
_STARTER_RE = re.compile(r'<div class="pit">\s*先発[：:]\s*(.*?)</div>', re.S)


def gameday(game: NpbGame) -> str:
    """'2026/0901' -- the date half of a game id, whether it has a slug yet.

    Keyed on rather than the clock because this runs for weeks, and a start
    announced tonight and then rained off must not silence the same man
    tomorrow.
    """
    return "/".join((game.game_id or "").split("/")[:2])


def parse_day_fixtures(html: str, day: str) -> list[Fixture]:
    """Every game the month page lists for ``day``, started or not.

    ``team1`` is the home side and ``team2`` the visiting one -- the same way
    round as the URL slug, and confirmed the same way, by the ballpark.
    """
    rows = re.compile(
        _FIXTURE_ROW_RE.pattern.replace("{mmdd}", f"{day[5:7]}{day[8:10]}"),
        re.S).findall(html or "")
    out: list[Fixture] = []
    for body in rows:
        sides = {which: _text(name) for which, name in _SIDE_RE.findall(body)}
        home, away = sides.get("1", ""), sides.get("2", "")
        starters = [_text(x) for x in _STARTER_RE.findall(body)]
        home_code, away_code = team_code(home), team_code(away)
        out.append(Fixture(
            away_team=team_name(away) or away,
            home_team=team_name(home) or home,
            starts_at=_start_time(body, day),
            home_starter=starters[0] if starters else "",
            away_starter=starters[1] if len(starters) > 1 else "",
            slug=f"{home_code}-{away_code}" if home_code and away_code else "",
        ))
    return out


def _start_time(body: str, day: str) -> dt.datetime | None:
    """'18:00' on the fixture's own date, in Japan. ``None`` if unprinted."""
    found = _TIME_RE.search(body)
    if not found:
        return None
    try:
        date = dt.date.fromisoformat(day)
    except ValueError:
        return None
    return dt.datetime(date.year, date.month, date.day,
                       int(found.group(1)) % 24, int(found.group(2)), tzinfo=JST)


def parse_game_links(html: str, day: str = "") -> list[str]:
    """Every game id linked from a page, in the order it lists them.

    Deduplicated but order-preserving: a page links the same game from its
    score, its team names and its detail link, and polling it three times
    would be three times the load on someone else's site for one game.

    ``day`` (``YYYY-MM-DD``) narrows it to one date, which is what the month
    schedule needs and what a single day's page does not.
    """
    wanted = f"{day[:4]}/{day[5:7]}{day[8:10]}" if day else ""
    seen: list[str] = []
    for year, mmdd, slug in _GAME_LINK_RE.findall(html or ""):
        game_id = f"{year}/{mmdd}/{slug}"
        if wanted and not game_id.startswith(wanted):
            continue
        if game_id not in seen:
            seen.append(game_id)
    return seen


# -- client ----------------------------------------------------------------
class NpbClient:
    """Polite scraper for npb.jp. Reuse one instance for the life of the poller.

    Two things keep the request count honest. The day's fixtures are fetched
    once and kept -- see :meth:`game_ids` -- and a game the page already calls
    試合終了 or 中止 is remembered and never fetched again: a finished game
    does not restart, and re-reading it every 30 seconds until midnight is
    pure waste on a site that is not being paid to serve it.
    """

    def __init__(self, timeout: int = 20, min_interval: float = 1.0) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        # game ids known to be over, per day.
        self._settled: dict[str, set[str]] = {}
        # (day, linked game ids, the day's fixtures, when) -- the month page,
        # both halves of it. See game_ids().
        self._index: tuple[str, list[str], list[Fixture], float] | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "ja,en;q=0.8",
            "User-Agent": "cpbl-alert (personal notification tool)",
        })

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.monotonic()

    def _get(self, path: str) -> str:
        self._throttle()
        resp = self.session.get(f"{BASE}{path}", timeout=self.timeout)
        resp.raise_for_status()
        # npb.jp serves EUC-JP on some pages and UTF-8 on others, and requests
        # guesses from the header alone. Let it read the meta tag too, or the
        # names come back as mojibake and every match silently fails.
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        body = resp.text
        # A CDN interstitial or an error page is still a 200 with a body, and
        # it would parse to an empty game -- which reads as "not live" and so
        # fails as *silence*, the one failure mode this feature cannot afford
        # to have go unnoticed. Say so instead.
        if "<" not in body[:2000]:
            raise NpbError(f"{path} did not return HTML")
        return body

    def schedule_page(self, day: str) -> str:
        """The month's fixture list, which is the index npb.jp will serve.

        ``/scores/<year>/<mmdd>/`` is the URL a day index ought to live at and
        it is not one: it answers 403 to any client, browser user-agent and
        referer included, while the game pages directly beneath it answer 200.
        The month page does work, and it carries every game's link with its
        date in it, so the day is a filter rather than a request.
        """
        return self._get(f"/games/{day[:4]}/schedule_{day[5:7]}_detail.html")

    def game_page(self, game_id: str) -> str:
        return self._get(f"/scores/{game_id}/")

    def game_ids(self, day: str) -> list[str]:
        """The day's games, re-read every :data:`INDEX_TTL` seconds.

        Not once a day, and not once a poll. The month page only links a game
        **once it has started** -- before first pitch it lists the fixture,
        the ballpark and the probable starter, and no link at all -- so a list
        taken at six o'clock would never grow to include the seven o'clock
        game. And it is 220KB, so taking it every thirty seconds would be a
        quarter of a megabyte an hour to re-read an answer that changes twice
        an evening.

        A refresh that comes back with nothing keeps the list it had, whether
        it failed outright or merely returned a page with no links in it. The
        list only ever grows through an evening, so an empty answer where
        there were six games is a bad minute on someone else's web server
        rather than news -- and acting on it would be a lane going quiet.
        """
        mine = self._index if (self._index and self._index[0] == day) else None
        known, fixtures = (mine[1], mine[2]) if mine else ([], [])
        if mine and (self.all_started(day)
                     or time.monotonic() - mine[3] < INDEX_TTL):
            return known
        try:
            page = self.schedule_page(day)
        except (NpbError, requests.RequestException) as exc:
            if not mine:
                raise
            log.warning("fixture refresh for %s failed, keeping %d game(s): %s",
                        day, len(known), exc)
            page = ""
        ids = parse_game_links(page, day) if page else []
        if not ids and known:
            # Keep the list, but keep the clock too: a page that has stopped
            # answering must not turn into a 220KB request every thirty
            # seconds for the rest of the night.
            self._index = (day, known, fixtures, time.monotonic())
            return known
        listed = parse_day_fixtures(page, day) if page else fixtures
        if ids != known:
            log.info("NPB %s: %d of %d game(s) under way",
                     day, len(ids), len(listed) or len(ids))
        self._index = (day, ids, listed, time.monotonic())
        return ids

    def fixtures(self, day: str) -> list[Fixture]:
        """The day's games as the month page lists them. Reads it if need be."""
        self.game_ids(day)
        mine = self._index if (self._index and self._index[0] == day) else None
        return mine[2] if mine else []

    def all_started(self, day: str) -> bool:
        """Has every fixture the day lists got a page of its own yet?

        Once it has there is nothing left for the index to tell us, so it is
        not read again -- which is the difference between reading 220KB twice
        an evening and reading it every two minutes until midnight.
        """
        mine = self._index if (self._index and self._index[0] == day) else None
        if not mine or not mine[2]:
            return False
        return all(f.under_way(mine[1]) for f in mine[2])

    def games(self, day: str) -> list[NpbGame]:
        """Every game on ``day``, with the live ones actually read.

        A game whose page fails is skipped rather than fatal: one bad page
        must not take the other five games down with it.
        """
        settled = self._settled.setdefault(day, set())
        ids = self.game_ids(day)
        stamp = f"{day[:4]}/{day[5:7]}{day[8:10]}"
        out: list[NpbGame] = []
        # A fixture the day lists but the header strip has not linked yet has
        # not started. It comes through as a game of its own -- without one an
        # evening before first pitch reads exactly like an evening with no
        # baseball in it, and there would be nothing to announce a starter
        # from.
        for fixture in self.fixtures(day):
            if fixture.under_way(ids):
                continue
            out.append(NpbGame(
                game_id=f"{stamp}/{fixture.slug}", status="pregame",
                away_team=fixture.away_team, home_team=fixture.home_team,
                starts_at=fixture.starts_at,
                away_starter=fixture.away_starter,
                home_starter=fixture.home_starter,
            ))
        for game_id in ids:
            if game_id in settled:
                continue
            try:
                game = parse_game_page(self.game_page(game_id), game_id)
            except (NpbError, requests.RequestException) as exc:
                log.warning("game %s failed: %s", game_id, exc)
                continue
            if game.status in ("final", "called"):
                settled.add(game_id)
            out.append(game)
        return out

    @staticmethod
    def live(games: list[NpbGame]) -> list[NpbGame]:
        return [g for g in games if g.status == "live"]


# -- the poller ------------------------------------------------------------
class TaiwaneseWatcher:
    """Poll NPB and push when a Taiwanese player is about to take the plate,
    or has taken the mound.

    The trigger is :mod:`cpbl_alert.stage`'s, exactly as on the MLB side: the
    pitcher fires on taking the mound, the batter on entering the two-slot
    window of at-the-plate and on-deck. It is keyed on the normalized *name*
    rather than on a person id, because the name is what both the order and
    the log print and it is what survives :func:`normalize` -- and two men
    with the same name do not bat back to back.

    The warning is longer here than in MLB, not shorter, and it comes free.
    npb.jp posts a plate appearance's result at the moment the next one
    begins, so the alert for the man at the plate goes out as he steps in,
    and the on-deck alert a full plate appearance before that.

    One buzz per appearance has a genuine exception here that MLB does not
    have. He is on deck with two out, the side is retired, and the other
    team bats -- at which point he is not in any window this watcher can see,
    so he is forgotten, and he is announced again when his side comes back
    and he leads off. That is two notifications about one trip to the plate,
    and it is the right two: the first said he was next and the inning ended
    instead, and the second is the one worth turning a television on for.
    """

    def __init__(
        self,
        feed: NpbFeed,
        notifier: Notifier,
        extra_players: list | None = None,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.feed = feed
        self.notifier = notifier
        self.poll_seconds = max(poll_seconds, MIN_POLL_SECONDS)
        self.dry_run = dry_run
        self.stages: dict[str, Stage] = {}
        # Starters already announced before their game, as (date, name).
        # A man named at half past is not named again at the hour when he
        # walks out and throws the first pitch: same event, and the early
        # notice was the point of knowing it early. The date is in the key
        # because this runs for weeks: a start that is rained off is never
        # taken back off a set keyed on the man alone, and he would go
        # unannounced every night after.
        self.announced: set = set()
        self.extra: dict[str, str] = {}
        for entry in extra_players or []:
            name = str(entry or "").strip()
            if name:
                # A config entry is already in the reader's own characters,
                # so it is its own display name.
                self.extra[normalize(name)] = name

    # -- membership --------------------------------------------------------
    def is_taiwanese(self, name: str) -> bool:
        key = normalize(name)
        return bool(key) and (key in _ROSTER or key in self.extra)

    def display(self, name: str) -> str:
        key = normalize(name)
        return _ROSTER.get(key) or self.extra.get(key) or (name or "").strip()

    # -- core --------------------------------------------------------------
    def process(self, games: list[NpbGame]) -> int:
        """Run one batch of games through the trigger. Returns alerts fired."""
        live = [g for g in games if g.status == "live"]
        live_ids = {g.game_id for g in live}
        # A game that has ended keeps no stage: a suspended game resumed
        # tomorrow should read as a fresh arrival, not a continuation.
        for gone in set(self.stages) - live_ids:
            self.stages.pop(gone, None)

        fired = 0
        for game in games:
            if game.status != "pregame":
                continue
            try:
                fired += self._process_upcoming(game)
            except Exception as exc:  # noqa: BLE001 - one bad game, not all six
                log.warning("fixture %s failed: %s", game.game_id, exc)
        for game in live:
            try:
                fired += self._process_game(game)
            except Exception as exc:  # noqa: BLE001 - one bad game, not all six
                log.warning("game %s failed: %s", game.game_id, exc)
        return fired

    def _process_upcoming(self, game: NpbGame, now: dt.datetime | None = None) -> int:
        """Announce a Taiwanese starter before his game. Returns alerts fired.

        Both sides are checked, because either club's 予告先発 could be ours.
        If both are, that is two notices rather than a duel: they are not
        facing each other, they are the two men starting tonight.

        npb.jp posts 予告先発 in the day rows of its month page. Whether it is
        there *before* first pitch could not be confirmed when this was
        written -- it is certainly there once a game is under way -- so this
        is written to be silent rather than wrong when the field is empty.
        ``cpbl-alert npb-probe`` prints the day's fixtures and what it found
        in them, which is how to settle it in one command.
        """
        if not self.due_soon(game, now):
            return 0
        fired = 0
        for name in (game.away_starter, game.home_starter):
            key = (gameday(game), normalize(name))
            if not key[1] or key in self.announced or not self.is_taiwanese(name):
                continue
            self.announced.add(key)
            self._fire_starter(game, name)
            fired += 1
        return fired

    @staticmethod
    def due_soon(game: NpbGame, now: dt.datetime | None = None) -> bool:
        """Is first pitch close enough to be worth a notification?

        Both ends matter. Tomorrow's game is not news yet, and a start time
        already past means the game is either under way -- in which case the
        man on the mound speaks for himself -- or delayed, which this would
        misreport as imminent.
        """
        if game.starts_at is None:
            return False
        now = now or now_jst()
        return dt.timedelta() <= game.starts_at - now <= dt.timedelta(
            minutes=STARTER_LEAD_MINUTES)

    def _fire_starter(self, game: NpbGame, name: str) -> None:
        upcoming = Upcoming(away_team=game.away_team, home_team=game.home_team,
                            starts_at=game.starts_at, name=self.display(name))
        text = format_starter_alert(upcoming)
        log.info("NPB STARTER %s | %s | %s at %s", game.game_id, upcoming.name,
                 upcoming.away_team, upcoming.home_team)
        if self.dry_run:
            print(text)
        else:
            self.notifier.send(text)

    def _process_game(self, game: NpbGame) -> int:
        state = state_from_npb_game(game)
        if state is None:
            return 0

        previous = self.stages.get(game.game_id)
        # The batter window, nearest first. Only Taiwanese players go in: the
        # stage records who has been *announced*, not who is standing there.
        window = [(role, name)
                  for role, name in (("batter", game.batter),
                                     ("on_deck", game.on_deck))
                  if self.is_taiwanese(name)]
        by_key = {normalize(name): (role, name) for role, name in window}
        pitcher_key = (normalize(game.pitcher)
                       if self.is_taiwanese(game.pitcher) else None)
        announced = (gameday(game), pitcher_key)
        if pitcher_key is not None and announced in self.announced:
            # He was named before the game and this is him walking out to
            # start it -- one event, one notification. Taken off the list so
            # that tomorrow's start, or a second stint, still speaks.
            self.announced.discard(announced)
            previous = Stage(batters=getattr(previous, "batters", frozenset()),
                             pitcher=pitcher_key,
                             duel=getattr(previous, "duel", None))
        # A duel is the pair, not either half of it, so it is keyed on both.
        duel_key = ((normalize(game.batter), pitcher_key)
                    if pitcher_key is not None and self.is_taiwanese(game.batter)
                    else None)
        self.stages[game.game_id] = Stage(batters=frozenset(by_key),
                                          pitcher=pitcher_key, duel=duel_key)

        # Taiwanese pitcher against Taiwanese batter: one notification, not
        # two -- but only when the pairing itself is the news. A new arm makes
        # a new matchup whoever is batting; a batter already announced from
        # the on-deck slot has had his buzz, and repeating it under a
        # different label when he steps in would be the second buzz this
        # exists to prevent. The MLB watcher settles it the same way.
        duel_now = changed(duel_key, previous, "duel") and (
            changed(pitcher_key, previous, "pitcher")
            or normalize(game.batter) not in getattr(previous, "batters",
                                                     frozenset()))
        if duel_now:
            self._fire(state, game, "duel", game.batter, detail="")
            return 1

        fired = 0
        for key in arrivals(by_key, previous):
            role, name = by_key[key]
            self._fire(state, game, role, name, detail=self._detail(game, role))
            fired += 1
        if changed(pitcher_key, previous, "pitcher"):
            self._fire(state, game, "pitcher", game.pitcher,
                       detail=self._detail(game, "pitcher"))
            fired += 1
        return fired

    def _detail(self, game: NpbGame, role: str) -> str:
        """What line four spends its remainder on.

        Where a batter sits in the order, which says roughly when he is up
        again -- and for the on-deck alert it is one slot on from the man at
        the plate, because that alert's whole subject is somebody else.

        A season average and a pitch count would both be better, and both are
        what ``batter_detail`` and ``pitcher_detail`` are for. Neither is on
        the game page: they live on ``box.html``, one tab across, which is a
        second request and so belongs where MLB puts its boxscore -- fetched
        only when something is about to be sent.
        """
        if role == "pitcher":
            return game.pitcher_detail
        if role == "batter" and game.batter_detail:
            return game.batter_detail
        slot = game.batting_order
        if not slot or not 1 <= slot <= 9:
            return ""
        return f"第{cn_number(next_slot(slot) if role == 'on_deck' else slot)}棒"

    def _fire(self, state: GameState, game: NpbGame, role: str, name: str,
              detail: str) -> None:
        spot = Spotlight(role=role, player_id=None,
                         name=self.display(name), detail=detail)
        text = format_stage_alert(state, spot)
        log.info("NPB ALERT %s | %s %s | %s",
                 game.game_id, role, spot.name, state.describe())
        if self.dry_run:
            print(text)
        else:
            self.notifier.send(text)

    # -- main loop ---------------------------------------------------------
    def _sleep_for(self, games: list[NpbGame], now: dt.datetime | None = None) -> int:
        """Poll pace when something is on, idle pace when nothing is close."""
        if any(g.status == "live" for g in games):
            return self.poll_seconds
        now = now or now_jst()
        if not ACTIVE_HOURS[0] <= now.hour < ACTIVE_HOURS[1]:
            return IDLE_SLEEP
        # Inside the window, a scheduled game that has not started yet is
        # worth staying awake for; a day of finished games is not. Slower
        # than the live pace on purpose: the only thing that can change
        # before first pitch is the 220KB index.
        return (PREGAME_POLL_SECONDS if any(g.status == "pregame" for g in games)
                else IDLE_SLEEP)

    def run(self, once: bool = False) -> None:
        while True:
            games: list[NpbGame] = []
            for day in window():
                try:
                    games.extend(self.feed.games(day))
                except (NpbError, requests.RequestException) as exc:
                    log.warning("fixtures for %s failed: %s", day, exc)
            if games:
                self.process(games)
            if once:
                return
            sleep = self._sleep_for(games)
            log.debug("%d game(s), %d live; sleeping %ss",
                      len(games), sum(g.status == "live" for g in games), sleep)
            time.sleep(sleep)


# -- the calendar ----------------------------------------------------------
def now_jst() -> dt.datetime:
    """Current time in Japan, independent of this machine's timezone."""
    return dt.datetime.now(JST)


def today_jst() -> str:
    return now_jst().strftime("%Y-%m-%d")


def window(now: dt.datetime | None = None) -> list[str]:
    """The JST date(s) that can hold a game happening right now.

    Unlike MLB this is normally one date, not two: an NPB game starts in the
    afternoon or evening and finishes the same JST day, and Taiwan is only an
    hour behind, so "tonight in Taipei" and "tonight in Tokyo" are the same
    date. The exception is the small hours -- a game that ran to a 12-inning
    finish past midnight is still filed under yesterday -- so before dawn the
    previous day is checked as well.
    """
    now = now or now_jst()
    days = [now.strftime("%Y-%m-%d")]
    if now.hour < 6:
        days.insert(0, (now - dt.timedelta(days=1)).strftime("%Y-%m-%d"))
    return days
