"""What "on stage" means, for whichever league is asking.

Two of the three watchers in this package answer the same question -- *is a
Taiwanese player about to be the one out there* -- against two completely
different feeds. What they genuinely share is not the feed and not the
payload but the **trigger**, and the small record of who we had already told
you about, both of which live here so the two cannot drift apart.

Everything else stays in the league module, because everything else is
league-shaped: MLB hands out person ids and a nationality field, NPB hands
out a batting order and a log of finished plate appearances.

**Why the batter trigger is a window rather than a slot.** The first version
fired the moment a Taiwanese player *became* the batter. That is the instant
his plate appearance starts -- and a plate appearance is over in two or three
minutes, so the alert routinely arrived in time for the reader to turn a
television on and watch somebody else bat. A pitcher has no such problem: he
is out there for innings, and an alert that finds you a minute late still
finds him on the mound.

So the batter trigger watches two slots, ``at the plate`` and ``on deck``,
and fires once for each Taiwanese player who *newly enters* that window. That
buys a full plate appearance of warning when the poll catches him on deck,
and it still fires -- late, rather than not at all -- when it does not, which
is the case that matters: two out, he is on deck, the inning ends, and next
inning he leads off having never been seen in the on-deck slot.

It stays one buzz per appearance because he does not leave the window when he
steps in. On-deck and then at the plate is the same key twice, and only a key
that was not there last time is an arrival.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Spotlight:
    """Who triggered the alert, and what to say about him on line four."""

    role: str            # "batter" | "on_deck" | "pitcher" | "duel"
    player_id: int | None
    name: str
    detail: str = ""     # 今日 1-2 / 今日 4.2局・7K / 第八棒 / nothing yet


@dataclass(frozen=True)
class Upcoming:
    """A game that has not started, and the Taiwanese arm down to start it.

    The one alert in this package that is not about a moment in a game, and
    so the one that cannot use :class:`Spotlight`: before first pitch there
    is no score, no inning, no diamond and nobody at the plate. What there is
    is two clubs, a time, and a name -- which is the whole of what a starter
    notice has to say.

    It exists because a pitcher's alert cannot be made earlier the way a
    batter's was. Nobody publishes a bullpen warming up, so on-the-mound is
    as early as a relief appearance can be known -- and it is early enough,
    because a reliever is named at the change and then faces at least one
    batter. A *starter* is different only in that he is knowable hours ahead,
    from the probable-starter fields both leagues already publish.
    """

    away_team: str = ""
    home_team: str = ""
    starts_at: dt.datetime | None = None    # aware, in whatever zone published
    name: str = ""                # the Taiwanese starter this is about


@dataclass
class Stage:
    """What we had already told you about one game, the last time we looked.

    Deliberately not a snapshot of the game: it is a record of what has been
    *said*. ``batters`` holds the Taiwanese players who were in the batter
    window -- at the plate or on deck -- and so have already been announced;
    ``pitcher`` and ``duel`` hold the occupant keys their own alerts were last
    sent for.

    The keys are whatever the league uses to tell one player from the next --
    a person id where the feed gives one, the name where it does not. Either
    works, because the only question ever asked of them is "same as last
    poll?".
    """

    batters: frozenset = field(default_factory=frozenset)
    pitcher: object = None
    duel: object = None


def arrivals(window: Iterable, previous: Stage | None) -> list:
    """The keys in the batter window that were not in it last poll.

    Order is the caller's: the league module lists the man at the plate
    before the man on deck, so an alert for someone standing in both -- which
    cannot happen -- would speak of the nearer one.

    On the first look at a game ``previous`` is ``None`` and nobody had been
    announced, so whoever is in the window counts as arriving. That is
    deliberate: a Taiwanese batter due up right now is the present tense, and
    it is the whole reason this exists.
    """
    was = getattr(previous, "batters", None) or frozenset()
    return [key for key in window if key not in was]


def changed(key: object, previous: Stage | None, slot: str) -> bool:
    """Has ``slot`` ("pitcher", "duel") changed hands since the last poll?

    ``key`` of ``None`` means the slot is not currently held by anyone we
    would announce, which is never an arrival.
    """
    if key is None:
        return False
    return getattr(previous, slot, None) != key
