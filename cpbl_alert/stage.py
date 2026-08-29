"""What "on stage" means, for whichever league is asking.

Two of the three watchers in this package answer the same question -- *is a
Taiwanese player the one out there right now* -- against two completely
different feeds. What they genuinely share is not the feed and not the
payload but the **trigger**: an alert fires when the man in a role becomes
someone he was not a moment ago. That rule, and the small record of who was
there last time, live here so the two cannot drift apart.

Everything else stays in the league module, because everything else is
league-shaped: MLB hands out person ids and a nationality field, NPB hands
out names in Japanese and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spotlight:
    """Who triggered the alert, and what to say about him on line four."""

    role: str            # "batter" | "pitcher" | "duel"
    player_id: int | None
    name: str
    detail: str = ""     # 今日 1-2 / 今日 4.2局・7K / 第八棒 / nothing yet


@dataclass
class Stage:
    """Who was on stage in one game the last time we looked.

    The two slots hold whatever the league uses to tell one player from the
    next -- a person id where the feed gives one, the name where it does not.
    Either works, because the only question ever asked of them is "same as
    last poll?".
    """

    batter: object = None
    pitcher: object = None


def arrivals(here: set[str], previous: Stage | None,
             now: dict[str, object]) -> set[str]:
    """Which of the roles in ``here`` just changed hands.

    ``here`` is the set of roles ("batter", "pitcher") currently held by a
    Taiwanese player; ``now`` maps every role to its current occupant key.
    An arrival is a role whose occupant differs from the one recorded in
    ``previous`` -- exactly one event per plate appearance and one per relief
    appearance, with no watermark to keep and nothing to dedupe. A pitcher
    who works three innings is one alert, not fifty.

    On the first look at a game ``previous`` is ``None`` and nobody was in
    any role, so whoever is standing there counts as arriving. That is
    deliberate: a Taiwanese pitcher on the mound right now is the present
    tense, and it is the whole reason this exists.
    """
    was = {"batter": getattr(previous, "batter", None),
           "pitcher": getattr(previous, "pitcher", None)}
    return {role for role in here
            if previous is None or was.get(role) != now.get(role)}
