"""Parsing CPBL live-log rows into a usable game state.

Field semantics were determined empirically against a full real game
(tests/fixtures/game290.json, 324 pitches) -- see docs in README:

  * One row == one pitch.
  * ``OutCnt`` and the base fields are PRE-pitch: they describe the situation
    the pitch was thrown into. An out produced by a pitch shows up on the
    NEXT row (verified on 32 of 34 out-producing pitches).
  * ``StrikeCnt``/``BallCnt`` are POST-pitch: the pitch's own ball/strike is
    already applied.

Consequence: the newest row is the state going *into* the most recent pitch,
so a chance created by a hit becomes visible one pitch later. The official
CPBL site's own scoreboard widget has exactly the same lag.
"""

from __future__ import annotations

from dataclasses import dataclass

# A base field holds the runner's batting-order slot as a string; empty means
# the base is unoccupied. This mirrors the official site's own template test
# (``on_base: curtDetail.FirstBase != ''``).
TOP = "1"


def _occupied(value: object) -> bool:
    return bool(str(value or "").strip())


@dataclass(frozen=True)
class GameState:
    """The situation on the field going into a given pitch."""

    game_sno: int
    year: str
    kind_code: str
    inning: int
    is_top: bool
    outs: int
    first: bool
    second: bool
    third: bool
    balls: int
    strikes: int
    visiting_score: int
    home_score: int
    batter: str
    pitcher: str
    event_no: str
    created_at: str
    visiting_team: str = ""
    home_team: str = ""

    # -- identity ----------------------------------------------------------
    @property
    def pitch_id(self) -> str:
        """Stable identity for this pitch, used as the no-repeat watermark.

        NOT ``Pkno``. The live log is regenerated server-side every 60-90
        seconds, and every ``Pkno`` in it is minted fresh each time: two polls
        either side of a rebuild share *zero* Pknos out of 185 rows, and the
        finished game 290 came back with different Pknos than the ones in
        tests/fixtures/game290.json. A watermark built on Pkno therefore
        evaporates about once a minute, the whole game replays as if new, and
        the same rally is pushed to the phone again and again.

        ``MainEventNo`` is structural instead of minted -- ``0610008000`` is
        inning 06, top half, the 8th event -- so it survives a rebuild
        unchanged. Its one known collision is the ``比賽結束`` marker, which
        repeats the final pitch's number; swallowing that row is right.

        What this does *not* cover: if a scorer inserts a missed pitch, every
        MainEventNo after it shifts and the rest of the game looks new. The
        watcher's "history never alerts" rule is what caps that to the live
        half-inning.
        """
        if self.event_no:
            return self.event_no
        # If CPBL ever drops MainEventNo, fall back to the situation itself.
        # Two rows with an identical count *and* identical bases/outs/score
        # are the same moment as far as an alert is concerned.
        return (f"{self.inning}|{self.is_top}|{self.outs}|{self.base_code()}|"
                f"{self.visiting_score}-{self.home_score}|{self.balls}-{self.strikes}|"
                f"{self.batter}|{self.pitcher}")

    # -- derived -----------------------------------------------------------
    @property
    def runners(self) -> int:
        return sum((self.first, self.second, self.third))

    @property
    def risp(self) -> bool:
        """Runner(s) in scoring position -- 得點圈."""
        return self.second or self.third

    @property
    def loaded(self) -> bool:
        return self.first and self.second and self.third

    @property
    def batting_score(self) -> int:
        return self.visiting_score if self.is_top else self.home_score

    @property
    def fielding_score(self) -> int:
        return self.home_score if self.is_top else self.visiting_score

    @property
    def deficit(self) -> int:
        """Runs the batting team trails by. Negative means it leads."""
        return self.fielding_score - self.batting_score

    @property
    def margin(self) -> int:
        return abs(self.visiting_score - self.home_score)

    @property
    def batting_team(self) -> str:
        return self.visiting_team if self.is_top else self.home_team

    @property
    def fielding_team(self) -> str:
        return self.home_team if self.is_top else self.visiting_team

    @property
    def half(self) -> str:
        return "上" if self.is_top else "下"

    def base_code(self) -> str:
        """Canonical base state, e.g. '1-3' or '123' or '---'."""
        return "".join(
            d if occ else "-"
            for d, occ in (("1", self.first), ("2", self.second), ("3", self.third))
        )

    def describe(self) -> str:
        return (
            f"{self.inning}局{self.half} {self.outs}出局 "
            f"{self.base_code()} {self.visiting_score}-{self.home_score}"
        )


def state_from_row(row: dict, meta: dict | None = None) -> GameState:
    """Build a :class:`GameState` from one raw CPBL live-log row."""
    meta = meta or {}
    return GameState(
        game_sno=int(row.get("GameSno") or 0),
        year=str(row.get("Year") or ""),
        kind_code=str(row.get("KindCode") or "A"),
        inning=int(row.get("InningSeq") or 0),
        is_top=str(row.get("VisitingHomeType")) == TOP,
        outs=int(row.get("OutCnt") or 0),
        first=_occupied(row.get("FirstBase")),
        second=_occupied(row.get("SecondBase")),
        third=_occupied(row.get("ThirdBase")),
        balls=int(row.get("BallCnt") or 0),
        strikes=int(row.get("StrikeCnt") or 0),
        visiting_score=int(row.get("VisitingScore") or 0),
        home_score=int(row.get("HomeScore") or 0),
        batter=str(row.get("HitterName") or ""),
        pitcher=str(row.get("PitcherName") or ""),
        event_no=str(row.get("MainEventNo") or ""),
        created_at=str(row.get("CreateTime") or ""),
        visiting_team=str(meta.get("VisitingTeamName") or ""),
        home_team=str(meta.get("HomeTeamName") or ""),
    )
