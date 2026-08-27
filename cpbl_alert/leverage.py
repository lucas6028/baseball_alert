"""Scoring how much a situation deserves to interrupt you.

Three factors are multiplied into a 0-100 ``tension`` score:

  1. ``situation``  -- how dangerous the base/out state is.
  2. ``urgency``    -- late innings amplify, early innings damp.
  3. ``closeness``  -- the score-margin gate: a rally only matters if the runs
     it can produce would change who wins.

**Why ``situation`` blends two tables.** Expected runs (RE24) is the natural
measure of how much damage a base/out state can do, and it is the right one
in a game that is still open. But late in a tight game you do not need runs
plural -- you need *one*, and the probability of scoring at least one run is
a very different curve (runner on 3rd with 1 out is mediocre by RE24 and
enormous by P(score >= 1)). Weighting only by RE24 undervalues exactly the
moments people care most about: a tie game in the 8th with the winning run
on second is worth waking up for even though its expected-run figure is
modest. So the two tables are blended, with the one-run table taking over as
the game gets late and close.

``closeness`` is built around the *tying run* -- the standard way baseball
talks about whether a rally is meaningful. With R runners on base, the batter
is potential run number R+1, so:

    trailing by D <= R          -> the tying run is already ON BASE
    trailing by D == R + 1      -> the tying run is AT THE PLATE
    trailing by D == R + 2      -> the tying run is ON DECK
    trailing by more            -> decays toward a floor

A team that is ahead gets a lead-dependent decay instead: protecting a
1-run lead is tense, padding a 7-run lead is not.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import GameState

# Expected runs in the remainder of the inning, by (base state, outs).
RUN_EXPECTANCY: dict[str, tuple[float, float, float]] = {
    "---": (0.48, 0.25, 0.10),
    "1--": (0.86, 0.51, 0.22),
    "-2-": (1.07, 0.66, 0.32),
    "--3": (1.31, 0.90, 0.36),
    "12-": (1.44, 0.89, 0.42),
    "1-3": (1.73, 1.16, 0.48),
    "-23": (1.95, 1.36, 0.58),
    "123": (2.29, 1.54, 0.75),
}

# Probability of scoring AT LEAST ONE run in the remainder of the inning.
# This is what matters when one run decides the game.
SCORE_ANY: dict[str, tuple[float, float, float]] = {
    "---": (0.26, 0.16, 0.07),
    "1--": (0.43, 0.27, 0.13),
    "-2-": (0.62, 0.42, 0.22),
    "--3": (0.85, 0.66, 0.27),
    "12-": (0.63, 0.42, 0.23),
    "1-3": (0.86, 0.65, 0.27),
    "-23": (0.86, 0.69, 0.27),
    "123": (0.87, 0.67, 0.32),
}

_MAX_RE = RUN_EXPECTANCY["123"][0]
_MAX_ANY = SCORE_ANY["123"][0]

# Inning multipliers. Extra innings inherit the 9th-inning weight.
_URGENCY = {1: 0.55, 2: 0.60, 3: 0.65, 4: 0.72,
            5: 0.80, 6: 0.88, 7: 0.97, 8: 1.05, 9: 1.15}

# How much the score margin makes "one run" the thing that matters.
_ONE_RUN_BY_MARGIN = {0: 1.0, 1: 1.0, 2: 0.5}
_ONE_RUN_DEFAULT = 0.15

DEFAULT_THRESHOLD = 55.0
WATCH_THRESHOLD = 38.0


@dataclass(frozen=True)
class Assessment:
    tension: float
    situation: float
    urgency: float
    closeness: float
    tier: str          # "alert" | "watch" | "quiet"
    reasons: tuple[str, ...]
    # Which branch of _closeness fired. The notifier re-voices these in PTT
    # slang; a tag keeps that translation from re-deriving -- and drifting
    # from -- the tying-run boundaries below.
    closeness_tag: str = "even"

    @property
    def should_alert(self) -> bool:
        return self.tier == "alert"


def _urgency(inning: int) -> float:
    if inning >= 9:
        return _URGENCY[9]
    return _URGENCY.get(inning, 0.55)


def _one_run_weight(inning: int, margin: int) -> float:
    """How much to trust P(score >= 1) over expected runs.

    Rises through the late innings and collapses when the margin is wide
    enough that a single run is irrelevant.
    """
    lateness = min(1.0, max(0.0, (inning - 5) / 4.0))
    return lateness * _ONE_RUN_BY_MARGIN.get(margin, _ONE_RUN_DEFAULT)


def _closeness(state: GameState) -> tuple[float, str, str]:
    """Score-margin gate. Returns (factor, human reason, tag)."""
    deficit = state.deficit          # >0 => batting team trails
    runners = state.runners

    if deficit == 0:
        return 1.0, "平手", "even"

    if deficit > 0:
        # Batting team is trying to come back.
        if deficit <= runners:
            return 1.0, f"追平分已在壘上 (落後{deficit}分)", "tying_on_base"
        if deficit == runners + 1:
            return 0.95, f"打者就是追平分 (落後{deficit}分)", "tying_at_plate"
        if deficit == runners + 2:
            return 0.60, f"追平分在下一棒 (落後{deficit}分)", "tying_on_deck"
        # Too far back for this rally to decide anything.
        return (max(0.12, 0.60 - 0.15 * (deficit - runners - 2)),
                f"落後{deficit}分，差距過大", "blowout")

    # Batting team leads; this is insurance, or the other side sweating.
    lead = -deficit
    return max(0.18, 1.0 - 0.18 * lead), f"領先{lead}分", "leading"


def assess(state: GameState, threshold: float = DEFAULT_THRESHOLD) -> Assessment:
    """Rate a single game situation."""
    outs = min(max(state.outs, 0), 2)
    code = state.base_code()

    expected = RUN_EXPECTANCY.get(code, RUN_EXPECTANCY["---"])[outs] / _MAX_RE
    one_run = SCORE_ANY.get(code, SCORE_ANY["---"])[outs] / _MAX_ANY

    w = _one_run_weight(state.inning, state.margin)
    situation = (1.0 - w) * expected + w * one_run

    urgency = _urgency(state.inning)
    closeness, close_reason, close_tag = _closeness(state)

    tension = min(100.0, 100.0 * situation * urgency * closeness)

    reasons: list[str] = []
    if state.loaded:
        reasons.append("滿壘")
    elif state.risp:
        bases = []
        if state.third:
            bases.append("三壘")
        if state.second:
            bases.append("二壘")
        reasons.append("得點圈有人（" + "、".join(bases) + "）")
    elif state.first:
        reasons.append("一壘有人")
    reasons.append(f"{state.outs}出局")
    if state.inning >= 7:
        reasons.append(f"{state.inning}局{state.half}（終盤）")
    reasons.append(close_reason)

    if tension >= threshold:
        tier = "alert"
    elif tension >= WATCH_THRESHOLD:
        tier = "watch"
    else:
        tier = "quiet"

    return Assessment(
        tension=round(tension, 1),
        situation=round(situation, 3),
        urgency=urgency,
        closeness=round(closeness, 3),
        tier=tier,
        reasons=tuple(reasons),
        closeness_tag=close_tag,
    )
