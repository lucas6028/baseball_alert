"""The PTT voice.

快轉台's reader is a 批踢踢 Baseball 板 regular, and that board has its own
register: 直播文 titled ``[LIVE]``, a ``※ 發信站`` footer, and 推/噓/→ marks
in front of every line of commentary. An alert written in newsroom Chinese
reads like a press release from the league; the same alert written the way
the board writes it reads like a friend who is already watching.

Four rules keep this from becoming decoration:

* **The slang carries the numbers, not just the mood.** 「落後3分，垃圾時間」
  still says "落後3分". Anyone who wants the raw figure never has to guess.
* **Every borrowed term carries the condition that makes it true.** 「劇場」
  is a *closer* blowing a lead, so it needs the 9th and a tying run on base;
  「開魯閣」 is ten runs allowed, so it needs ten runs on the board; 「問天」
  is a starter who got no support, so it is about 先發 and never about
  ``state.pitcher`` -- that man plays for the other team. A term used in the
  wrong situation is how you tell the board you do not actually watch
  baseball, so each condition lives in ``_EXTRAS`` next to its words.
* **Nobody gets mocked by name.** The board's dictionary is full of terms
  built out of one real player's bad night (國慶球, 國輝球, 玉山大曲, 院長,
  瓜, 坦) and of fanbase slurs and umpire-bias accusations. They are genuine
  slang and they are deliberately absent: a push notification that calls a
  named professional a punchline is not a joke the reader opted into. What
  is kept describes the *situation* -- 劇場, 問天, 殘壘, 坐牢, 大中計.
* **The analysis stays analytic.** ``leverage.Assessment.reasons`` is what
  ``cpbl-alert check`` prints when you are tuning a threshold, so it keeps
  its plain wording. This module re-voices the *situation*, and takes the
  score-margin branch from ``closeness_tag`` rather than re-deriving the
  tying-run boundaries -- one source of truth for the math.

Variant choice is a CRC of the game situation, not ``random``: the same
pitch always produces the same line (so replays and tests are stable) while
consecutive alerts in a game still sound different.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass

from .leverage import Assessment
from .models import GameState

BOARD_TAG = "[LIVE]"
SITE = "ptt.cc"

# 推 = agree/hype, 噓 = boo, → = neutral aside. Same three marks the board uses.
PUSH, BOO, ARROW = "推", "噓", "→"

# How the board actually types team names -- nobody writes 統一7-ELEVEn獅 in a
# 直播文. Unknown or postseason-renamed teams fall through to the full name.
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

# Banded so the words line up with the alert range: the default threshold is
# 55, so anything that actually reaches a phone reads as at least 先卡個位.
_TENSION_WORDS = (
    (90.0, "爆"),
    (72.0, "這篇會爆"),
    (55.0, "先卡個位"),
    (0.0, "有搞頭"),
)

# -- phrase tables ---------------------------------------------------------
# Every entry is a tuple of interchangeable variants; _pick chooses one.
# These are the lines that hold true anywhere the slot can be reached.
# Anything that needs a condition lives in _EXTRAS instead.

_BASES: dict[str, tuple[str, ...]] = {
    "123": ("滿壘啦，壘上塞好塞滿",
            "滿壘，一支就清空",
            "壘包上站滿人，就等這一棒"),
    "-23": ("二三壘有人，一安打回來兩分",
            "得點圈站兩個，一棒翻盤"),
    "1-3": ("一三壘有人，跑者隨時要動",
            "一三壘有人，內野要頭痛了"),
    "12-": ("一二壘有人，棒次串起來了",
            "一二壘有人，再一支就滿壘"),
    "--3": ("三壘有人，一棒就送他回來",
            "三壘有人，只剩九十呎"),
    "-2-": ("二壘有人，得點圈有肉",
            "跑者站上得點圈，一安就回來"),
    "1--": ("一壘有人，先把棒次串起來",
            "一壘有人，等著被送上得點圈"),
    "---": ("壘上還沒人，但這棒是開路的",
            "壘上空空，就看這棒能不能上"),
}

_OUTS: dict[int, tuple[str, ...]] = {
    0: ("無人出局，這局有得看", "無人出局，慢慢磨他"),
    1: ("一出局，還有兩次機會", "一出局，這局還沒完"),
    2: ("兩出局，一棒定生死", "兩出局，成敗就這一球"),
}

# 雙殺 only exists when there is a force at second, so this is registered as a
# conditional variant below rather than living in _OUTS -- 「別打成雙殺」 with
# an empty first base is the kind of line that tells the board you do not
# actually watch baseball.
_DOUBLE_PLAY: tuple[str, ...] = ("一出局，別打成雙殺就好",)

# 再見 only exists in a half-inning the batting team can *end*, so the walk-off
# wording is confined to the bottom-half keys. Extra innings are split for the
# same reason: the top of the 10th cannot end anything.
_LATE: dict[str, tuple[str, ...]] = {
    "extra_top": ("延長賽加班中，這半局先得分才有籌碼",
                  "延長賽了，先馳得點的壓力都在這半局"),
    "extra_bottom": ("延長賽下半，一分就再見",
                     "延長賽加班，這半局得分就散場"),
    "9下":  ("九下，再見安打預定",
             "九下，這半局打完就散場",
             "九下，一發再見砲直接結束"),
    "9上":  ("九上，剩最後三個出局數",
             "九上，沒有下一次了"),
    "8":    ("八局，勝負手就在這裡",
             "八局，牛棚該上工了"),
    "7":    ("七局，終盤開始",
             "七局過後每一球都算分"),
}

_CLOSENESS: dict[str, tuple[str, ...]] = {
    "even": ("平手，誰先得分誰爽",
             "平手，這一分就是勝分"),
    "tying_on_base": ("追平分已經站上壘包，逆轉就靠這棒",
                      "追平分在壘上了，回來就重新開始"),
    "tying_at_plate": ("打者本人就是追平分，一發就平手",
                       "這棒站上去就是追平分"),
    "tying_on_deck": ("追平分排在下一棒，先把人送上去",
                      "追平分在後面等，這棒負責串連"),
    "blowout": ("落後{n}分，說是垃圾時間也不為過",
                "落後{n}分，看爽的就好"),
    "leading": ("領先{n}分，牛棚千萬別放火",
                "領先{n}分，守成就好不要演"),
}

_CTA: tuple[str, ...] = (
    "還不轉台==",
    "先卡位，等等一定爆",
    "快轉台，這局不能錯過",
    "這局看完再睡",
    "朝聖，順便求個逆轉",
    "先M起來，這局有東西",
    "神串留名，等等回來看結果",
    "別當烏鴉，安靜轉台就好",
)

# 噓 is for the branches the board would boo: a lead being nursed, or a
# deficit big enough that the rally is entertainment rather than drama.
_BOO_TAGS = frozenset({"blowout", "leading"})

# The branches where the batting team is still playing for the win. 問天 is
# about an offence wasting a good start, which stops being the story once the
# game is out of reach in either direction.
_COMEBACK_TAGS = frozenset({"even", "tying_on_base", "tying_at_plate",
                            "tying_on_deck"})


# Every line push_lines can build. A slot named here is one push_lines
# actually consults, and _Extra rejects anything else at import time -- an
# extra registered against a slot nobody reads would otherwise be dead
# phrases: rendered nowhere, raising nothing, passing every test.
_SLOTS = ("bases", "outs", "late", "close", "cta")


# -- conditional slang -----------------------------------------------------
@dataclass(frozen=True)
class _Extra:
    """A phrase that is only *correct* in some situations.

    ``slot`` names the line it can appear on, ``when`` is the condition that
    makes the term true, and ``lines`` are the variants it contributes. The
    condition sits next to the words on purpose: the board's borrowed terms
    each encode one specific game state, and a term separated from its state
    is the exact failure this table exists to prevent.
    """

    slot: str
    when: Callable[[GameState, Assessment], bool]
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.slot not in _SLOTS:
            raise ValueError(
                f"unknown slot {self.slot!r}; push_lines only reads {_SLOTS}")


_EXTRAS: tuple[_Extra, ...] = (
    # -- bases -------------------------------------------------------------
    _Extra(
        "bases", lambda s, a: s.loaded,
        # 滿貫砲 clears exactly the bases that are loaded, so this promises
        # nothing about the score. 大中計 (滿壘大中計) is the board's name for
        # a bases-loaded chance that produces nothing -- in a 直播文 it is said
        # as a fear while the chance is still alive, which is exactly here.
        # 煮粥 is 錢定遠's 「亂成一鍋粥」: a defence coming apart.
        ("滿壘，一發滿貫砲全部帶回來",
         "滿壘，別又滿壘大中計",
         "滿壘，對面守備一煮粥就送分"),
    ),
    # 殘壘 is the standing fear from the moment anyone reaches scoring position.
    # 歐巴 is the board's spelling of "over" -- a ball hit over the outfielder.
    # It needs a runner already in scoring position for the promise to hold.
    _Extra(
        "bases", lambda s, a: s.risp,
        ("得點圈站著人，別又殘壘收場",
         "得點圈有人，一發歐巴就送回來"),
    ),

    # -- outs --------------------------------------------------------------
    _Extra("outs", lambda s, a: s.outs == 1 and s.first, _DOUBLE_PLAY),

    # -- closeness ---------------------------------------------------------
    # 劇場: the man on the mound holds a lead and gets into trouble anyway.
    # That is the *pitching* side of tying_on_base -- and only from the 9th,
    # because a jam in the 7th is not a closer and the board hears the
    # difference.
    _Extra(
        "close",
        lambda s, a: a.closeness_tag == "tying_on_base" and s.inning >= 9,
        ("追平分站上壘包，劇場開演了",
         "追平分上壘，對面牛棚劇場加演"),
    ),
    # tying_at_plate means the batter is potential run number D, so a home run
    # ties it exactly -- 炸裂 is safe to promise here and nowhere else.
    _Extra(
        "close", lambda s, a: a.closeness_tag == "tying_at_plate",
        ("打者本人就是追平分，一發炸裂剛好平手",),
    ),
    # 問天: a starter pitched well and the offence gave him nothing. It is
    # always about 自家先發 -- never 「投手」, which in a live thread means the
    # man currently on the mound, i.e. the other team's.
    _Extra(
        "close",
        lambda s, a: (a.closeness_tag in _COMEBACK_TAGS
                      and s.batting_score <= 1 and s.inning >= 7),
        ("打線再熄火，自家先發就要問天了",
         "再不得分，先發投再好也只能問天"),
    ),
    _Extra(
        "close", lambda s, a: a.closeness_tag == "blowout",
        ("落後{n}分，坐牢中",
         "落後{n}分，這場基本上涼了"),
    ),
    # 圖書館 is a *home* crowd gone quiet, so it needs the home team to be the
    # one trailing -- i.e. the home team at bat.
    _Extra(
        "close",
        lambda s, a: a.closeness_tag == "blowout" and not s.is_top,
        ("落後{n}分，主場安靜到變圖書館",),
    ),
    # 開魯閣 is ten runs allowed. Gate on the runs actually on the board, which
    # is what the term literally counts, rather than on the deficit.
    _Extra(
        "close",
        lambda s, a: a.closeness_tag == "blowout" and s.fielding_score >= 10,
        ("落後{n}分，自家投手開魯閣了",),
    ),
    _Extra(
        "close", lambda s, a: a.closeness_tag == "leading",
        # 砸鍋 = a reliever blowing it; 骰子牛 = a bullpen you have to roll for.
        # 「穩了」 is the board's jinx: it only ever gets typed before a collapse.
        ("領先{n}分，牛棚別砸鍋",
         "領先{n}分，穩了啦（X）",
         "領先{n}分，骰子牛不要開骰"),
    ),
    # 關門 is what a closer does, so it needs a 9th inning to close.
    _Extra(
        "close",
        lambda s, a: a.closeness_tag == "leading" and s.inning >= 9,
        ("領先{n}分，關門收工就好",),
    ),
)


def all_phrases() -> tuple[str, ...]:
    """Every string this module can put in front of a reader.

    The safety sweep in the tests walks this instead of a hand-written list of
    tables, so adding a table cannot quietly skip the checks that decide
    whether an alert is sendable at all: a stray ``<`` makes Telegram's
    ``parse_mode=HTML`` return 400, and a stray brace blows up ``.format``.
    """
    tables = (*_BASES.values(), *_OUTS.values(), *_LATE.values(),
              *_CLOSENESS.values(), _CTA,
              *(extra.lines for extra in _EXTRAS),
              tuple(TEAM_ALIASES.values()),
              tuple(word for _, word in _TENSION_WORDS))
    return tuple(phrase for table in tables for phrase in table)


# -- helpers ---------------------------------------------------------------
def team(name: str) -> str:
    """Board shorthand for a team, falling through to the full name."""
    name = (name or "").strip()
    if name in TEAM_ALIASES:
        return TEAM_ALIASES[name]
    for full, alias in TEAM_ALIASES.items():
        if full and full in name:
            return alias
    return name


def cn_number(n: int) -> str:
    """1 -> 一, 10 -> 十, 12 -> 十二. Innings only ever get this small."""
    if n < 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n - 10] if n > 10 else "")
    return str(n)


def inning_label(state: GameState) -> str:
    """九上 / 十二下 -- how a 直播文 writes the half-inning."""
    return f"{cn_number(state.inning)}{state.half}"


def outs_label(outs: int) -> str:
    return _CN_OUTS[outs] if 0 <= outs <= 2 else f"{outs}出局"


def tension_word(tension: float) -> str:
    """A 推文-count verdict to sit beside the number ('爆' at the top end)."""
    for floor, word in _TENSION_WORDS:
        if tension >= floor:
            return word
    return _TENSION_WORDS[-1][1]


def _seed(state: GameState, slot: str) -> int:
    """Stable per-situation seed. Salted per slot so the picks don't rhyme."""
    key = (f"{slot}|{state.game_sno}|{state.inning}|{state.is_top}|"
           f"{state.outs}|{state.base_code()}|{state.pkno}")
    return zlib.crc32(key.encode("utf-8"))


def _pick(options: tuple[str, ...], state: GameState, slot: str) -> str:
    return options[_seed(state, slot) % len(options)]


def _with_extras(slot: str, base: tuple[str, ...], state: GameState,
                 assessment: Assessment) -> tuple[str, ...]:
    """``base`` plus every conditional variant whose situation actually holds.

    ``_EXTRAS`` is a tuple and has to stay one. Building this from a set would
    make the order depend on ``PYTHONHASHSEED``, so the CRC would land on a
    different line in a different process -- a reproducibility bug no
    single-process test can see, because both calls would agree with each
    other while disagreeing with yesterday's replay.
    """
    extra = tuple(line
                  for entry in _EXTRAS
                  if entry.slot == slot and entry.when(state, assessment)
                  for line in entry.lines)
    return base + extra


def _late_key(state: GameState) -> str | None:
    if state.inning >= 10:
        return "extra_top" if state.is_top else "extra_bottom"
    if state.inning == 9:
        return "9上" if state.is_top else "9下"
    if state.inning == 8:
        return "8"
    if state.inning == 7:
        return "7"
    return None


# -- the comment thread ----------------------------------------------------
def push_lines(state: GameState, assessment: Assessment) -> list[str]:
    """The 推文 under the alert: why this moment, in the board's words.

    Ends with the call to action, because on PTT the last push is the one
    you read. The line *count* is set by the situation, never by how much
    slang happens to apply: a conditional term competes for an existing line
    rather than adding one, so a phone preview keeps the same shape.
    """
    outs = min(max(state.outs, 0), 2)

    bases = _with_extras("bases", _BASES[state.base_code()], state, assessment)
    out_options = _with_extras("outs", _OUTS[outs], state, assessment)

    lines = [f"{PUSH} {_pick(bases, state, 'bases')}",
             f"{ARROW} {_pick(out_options, state, 'outs')}"]

    late = _late_key(state)
    if late:
        late_options = _with_extras("late", _LATE[late], state, assessment)
        lines.append(f"{ARROW} {_pick(late_options, state, 'late')}")

    # A closeness branch added to leverage.py without a phrase here degrades
    # to the analytic wording rather than killing the alert -- losing the
    # slang on one line beats losing the 9th-inning push entirely. Extras hang
    # off a *known* tag, so that degrade path stays untouched.
    tag = assessment.closeness_tag
    mark = BOO if tag in _BOO_TAGS else PUSH
    options = _CLOSENESS.get(tag)
    if options:
        options = _with_extras("close", options, state, assessment)
        n = abs(state.deficit)
        lines.append(f"{mark} {_pick(options, state, 'close').format(n=n)}")
    elif assessment.reasons:
        lines.append(f"{mark} {assessment.reasons[-1]}")

    cta = _with_extras("cta", _CTA, state, assessment)
    lines.append(f"{PUSH} {_pick(cta, state, 'cta')}")
    return lines


def headline(state: GameState) -> str:
    """The 直播文 title: [LIVE] plus the scoreboard, in board shorthand."""
    return (f"{BOARD_TAG} {team(state.visiting_team)} {state.visiting_score}"
            f"-{state.home_score} {team(state.home_team)}")


def footer(brand: str) -> str:
    return f"※ 發信站: {brand}({SITE})"
