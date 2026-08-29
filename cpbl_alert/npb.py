"""NPB: buzz when a Taiwanese player is the one on stage.

Same question as :mod:`cpbl_alert.mlb`, same four lines on the phone, same
trigger from :mod:`cpbl_alert.stage` -- and almost nothing else in common,
because Japan gives you none of the three things that made the MLB side easy.

  1. **There is no API.** MLB hands the whole league's live situation over in
     one JSON call; NPB publishes HTML for people to read. So this file
     scrapes, and it pays a request per live game on top of the day's
     scoreboard -- which is why the default poll here is 30s rather than 20s
     and why a game the scoreboard already calls finished is never fetched.

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
from dataclasses import dataclass
from typing import Protocol

import requests

from .models import GameState
from .notifier import Notifier, cn_number, format_stage_alert
from .stage import Spotlight, Stage, arrivals

log = logging.getLogger(__name__)

BASE = "https://npb.jp"

MIN_POLL_SECONDS = 15
# Slower than MLB's 20s on purpose: a tick here costs one scoreboard request
# plus one per live game, against a site rather than an API.
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
    batter: str = ""                   # as NPB printed it
    pitcher: str = ""
    batting_order: int | None = None
    batter_detail: str = ""            # 打率 .275
    pitcher_detail: str = ""           # 投 87球
    starts_at: dt.datetime | None = None


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
# Every site-specific assumption in this file lives in this block, and every
# rule in it is anchored on a Japanese label rather than on markup. Each field
# lists its patterns in preference order; the first that matches wins, and a
# field nothing matches keeps its default rather than failing the parse -- a
# missing ball count should cost the count, not the alert.
#
# `cpbl-alert npb-probe <slug>` reports which rule matched what, which is the
# intended way to correct any of this against the live site.

_TAG_RE = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")

# npb.jp's own game links: /scores/2026/0829/f-l-01/
_GAME_LINK_RE = re.compile(r"/scores/(\d{4})/(\d{4})/([a-z]{1,2}-[a-z]{1,2}-\d{1,2})/")
# The slug is visitor-home-number, which is the order the site lists a game in
# ("f-l-01" is Fighters at Lions). npb-probe prints both sides of this so it
# can be confirmed rather than trusted.
_SLUG_RE = re.compile(r"^([a-z]{1,2})-([a-z]{1,2})-(\d{1,2})$")

FIELD_PATTERNS: dict[str, tuple[str, ...]] = {
    "inning": (r"(\d{1,2})\s*回\s*(表|裏)",),
    "outs": (r"(\d)\s*アウト", r"アウト\s*[：:]\s*(\d)", r"\bO\s*[：:]\s*(\d)\b"),
    "balls": (r"ボール\s*[：:]?\s*(\d)", r"\bB\s*[：:]\s*(\d)\b"),
    "strikes": (r"ストライク\s*[：:]?\s*(\d)", r"\bS\s*[：:]\s*(\d)\b"),
    # A name is CJK or Latin letters and never a digit, which is what keeps
    # "打者 3-1" from being read as a player called "3".
    "batter": (r"打\s?者\s{0,4}[：:]?\s{0,4}([^\s\d：:<]{2,12})",
               r"バッター\s{0,4}[：:]?\s{0,4}([^\s\d：:<]{2,12})"),
    "pitcher": (r"投\s?手\s{0,4}[：:]?\s{0,4}([^\s\d：:<]{2,12})",
                r"ピッチャー\s{0,4}[：:]?\s{0,4}([^\s\d：:<]{2,12})"),
    "batting_order": (r"([1-9])\s*番\b",),
    "average": (r"打率\s*[：:]?\s*(\.\d{3}|\d\.\d{3})",),
    "pitch_count": (r"(?:投球数|球数)\s*[：:]?\s*(\d{1,3})",),
}

# A runner is named on his base, so the base is occupied when the label has
# something after it that is not the word for empty.
_RUNNER_RE = re.compile(r"([一二三])\s?塁\s{0,4}[：:]?\s{0,4}([^\s：:<]{1,12})")
_NO_RUNNER = frozenset({"なし", "無し", "ー", "-", "―", "－"})

# The lookarounds are what keep a date out of the scoreboard: without them
# "2026-08-29" parses as an 8-29 final. MAX_RUNS is the second guard -- NPB's
# record for a single side is 28, so anything above this is not a score.
_SCORE_RE = re.compile(r"(?<![\d.\-/年月])(\d{1,2})\s?[-‐‑–—ー－]\s?(\d{1,2})"
                       r"(?![\d.\-/年月日])")
MAX_RUNS = 30


def parse_score(text: str) -> tuple[int, int]:
    """The first pair of numbers on the page that could be a baseball score."""
    for away, home in _SCORE_RE.findall(text):
        if int(away) <= MAX_RUNS and int(home) <= MAX_RUNS:
            return int(away), int(home)
    return 0, 0

STATUS_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("called", ("中止", "ノーゲーム", "サスペンデッド")),
    ("final", ("試合終了", "ゲームセット")),
    ("pregame", ("試合前", "試合開始前", "先発予告")),
)


def strip_tags(html: str) -> str:
    """HTML to the text a reader would see, one field per line where it can be.

    Block boundaries become newlines *before* the tags go, because 打者 and
    the name after it usually sit in adjacent cells: run them together
    without a separator and the label matches into the middle of a word.
    """
    text = re.sub(r"(?i)<\s*(br|/tr|/td|/th|/div|/p|/li|/h[1-6])\b[^>]*>",
                  "\n", html or "")
    text = _TAG_RE.sub(" ", text)
    text = html_mod.unescape(text)
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    # Blank runs collapse, because the label rules above bound how far a
    # label may reach for its value and that bound is only meaningful if the
    # gap between two adjacent cells is a fixed size.
    return "\n".join(line for line in lines if line)


def match_rule(text: str, field: str) -> re.Match | None:
    """The first rule for ``field`` that matches, or ``None``.

    Public because ``npb-probe`` reports rule by rule: showing which rule
    fired, and on what, is the only way to correct :data:`FIELD_PATTERNS`
    against a page nobody here can see.
    """
    for pattern in FIELD_PATTERNS[field]:
        found = re.search(pattern, text)
        if found:
            return found
    return None


def _int_field(text: str, name: str, default: int = 0) -> int:
    found = match_rule(text, name)
    try:
        return int(found.group(1)) if found else default
    except (TypeError, ValueError):
        return default


def _str_field(text: str, name: str) -> str:
    found = match_rule(text, name)
    return found.group(1).strip() if found else ""


def parse_status(text: str) -> str:
    """Where the game is, from the words the page uses for it.

    Order matters: a called game says 中止 and a finished one 試合終了, but a
    page can carry the *inning* of a game that has since ended, so the tokens
    are checked before the inning is believed.
    """
    for status, tokens in STATUS_TOKENS:
        if any(token in text for token in tokens):
            return status
    return "live" if re.search(FIELD_PATTERNS["inning"][0], text) else "pregame"


def parse_bases(text: str) -> tuple[bool, bool, bool]:
    """Which bases have a runner on them, read off the runner labels."""
    bases = {"一": False, "二": False, "三": False}
    for base, who in _RUNNER_RE.findall(text):
        bases[base] = who.strip() not in _NO_RUNNER
    return bases["一"], bases["二"], bases["三"]


def teams_from_slug(slug: str) -> tuple[str, str]:
    """('火腿', '西武') from 'f-l-01'. ('', '') if the slug is not one."""
    found = _SLUG_RE.match((slug or "").strip().lower())
    if not found:
        return "", ""
    return team_name(found.group(1)), team_name(found.group(2))


def parse_game_page(html: str, game_id: str) -> NpbGame:
    """One game page -> one :class:`NpbGame`.

    Pure: it takes a string and returns a dataclass, which is what makes the
    whole feed testable without a network and what makes ``npb-probe`` able
    to show its work.
    """
    text = strip_tags(html)
    slug = game_id.rsplit("/", 1)[-1]
    away, home = teams_from_slug(slug)

    inning_hit = match_rule(text, "inning")
    inning = int(inning_hit.group(1)) if inning_hit else 0
    is_top = inning_hit.group(2) == "表" if inning_hit else True

    away_score, home_score = parse_score(text)

    order_raw = _str_field(text, "batting_order")
    average = _str_field(text, "average")
    pitches = _str_field(text, "pitch_count")
    first, second, third = parse_bases(text)

    return NpbGame(
        game_id=game_id,
        status=parse_status(text),
        away_team=away,
        home_team=home,
        inning=inning,
        is_top=is_top,
        outs=_int_field(text, "outs"),
        balls=_int_field(text, "balls"),
        strikes=_int_field(text, "strikes"),
        away_score=away_score,
        home_score=home_score,
        first=first,
        second=second,
        third=third,
        batter=_str_field(text, "batter"),
        pitcher=_str_field(text, "pitcher"),
        batting_order=int(order_raw) if order_raw else None,
        batter_detail=f"打率 {average}" if average else "",
        pitcher_detail=f"投 {pitches}球" if pitches else "",
    )


def parse_scoreboard(html: str) -> list[str]:
    """The day's game ids, in the order the scoreboard lists them.

    Deduplicated but order-preserving: a scoreboard links the same game from
    its score, its team names and its 詳細 link, and polling it three times
    would be three times the load on someone else's site for one game.
    """
    seen: list[str] = []
    for year, mmdd, slug in _GAME_LINK_RE.findall(html or ""):
        game_id = f"{year}/{mmdd}/{slug}"
        if game_id not in seen:
            seen.append(game_id)
    return seen


# -- client ----------------------------------------------------------------
class NpbClient:
    """Polite scraper for npb.jp. Reuse one instance for the life of the poller.

    Two things keep the request count honest. The scoreboard is one request
    for the whole day, and a game it already calls 試合終了 or 中止 is
    remembered and never fetched again -- a finished game does not restart,
    and re-reading it every 30 seconds until midnight is pure waste on a site
    that is not being paid to serve it.
    """

    def __init__(self, timeout: int = 20, min_interval: float = 1.0) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        # game ids known to be over, per day.
        self._settled: dict[str, set[str]] = {}
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

    def scoreboard(self, day: str) -> str:
        """The day's scoreboard page. ``day`` is ``YYYY-MM-DD`` in JST."""
        return self._get(f"/scores/{day[:4]}/{day[5:7]}{day[8:10]}/")

    def game_page(self, game_id: str) -> str:
        return self._get(f"/scores/{game_id}/")

    def game_ids(self, day: str) -> list[str]:
        return parse_scoreboard(self.scoreboard(day))

    def games(self, day: str) -> list[NpbGame]:
        """Every game on ``day``, with the live ones actually read.

        A game whose page fails is skipped rather than fatal: one bad page
        must not take the other five games down with it.
        """
        settled = self._settled.setdefault(day, set())
        out: list[NpbGame] = []
        for game_id in self.game_ids(day):
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
    """Poll NPB and push when a Taiwanese player takes the plate or the mound.

    The trigger is :func:`cpbl_alert.stage.arrivals`, exactly as on the MLB
    side, and it is keyed on the *name* rather than on a person id because
    NPB does not publish one. That is enough: a plate appearance ends when
    the name in the batter slot changes, and two men with the same name do
    not bat back to back.
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
        for game in live:
            try:
                fired += self._process_game(game)
            except Exception as exc:  # noqa: BLE001 - one bad game, not all six
                log.warning("game %s failed: %s", game.game_id, exc)
        return fired

    def _process_game(self, game: NpbGame) -> int:
        state = state_from_npb_game(game)
        if state is None:
            return 0

        previous = self.stages.get(game.game_id)
        occupants = {"batter": normalize(game.batter),
                     "pitcher": normalize(game.pitcher)}
        self.stages[game.game_id] = Stage(occupants["batter"], occupants["pitcher"])

        names = {"batter": game.batter, "pitcher": game.pitcher}
        here = {role for role, name in names.items() if self.is_taiwanese(name)}
        arrived = arrivals(here, previous, occupants)
        if not arrived:
            return 0

        # Taiwanese pitcher against Taiwanese batter: one notification, not
        # two. Sending both would buzz twice for a single moment and make
        # each of them look like it was about someone else.
        if len(here) == 2:
            self._fire(state, game, "duel", game.batter, detail="")
            return 1

        for role in sorted(arrived):
            self._fire(state, game, role, names[role],
                       detail=self._detail(game, role))
        return len(arrived)

    def _detail(self, game: NpbGame, role: str) -> str:
        """What line four spends its remainder on.

        The page gives a season average for the man at the plate and a pitch
        count for the man on the mound, both of which answer the question you
        would ask next. Before a batter's first trip up there is neither, so
        the fallback is where he sits in the order -- which says roughly when
        he is up again.
        """
        if role == "pitcher":
            return game.pitcher_detail
        if game.batter_detail:
            return game.batter_detail
        slot = game.batting_order
        return f"第{cn_number(slot)}棒" if slot and 1 <= slot <= 9 else ""

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
        # worth staying awake for; a day of finished games is not.
        return (self.poll_seconds if any(g.status == "pregame" for g in games)
                else IDLE_SLEEP)

    def run(self, once: bool = False) -> None:
        while True:
            games: list[NpbGame] = []
            for day in window():
                try:
                    games.extend(self.feed.games(day))
                except (NpbError, requests.RequestException) as exc:
                    log.warning("scoreboard for %s failed: %s", day, exc)
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
