"""A faster live-state source for CPBL: playsport.cc's scoreboard feed.

Why this exists at all. The official CPBL live log is not pushed, it is
*rebuilt* server-side every 70-110 seconds, so ``client.py`` can poll every
15s and still be looking at a minute-old game. Measured against the same
situations on 2026-09-04, playsport published them 33-119 seconds earlier.
For a thing whose entire premise is "turn the TV on *now*", a minute is the
difference between catching the at-bat and catching the replay.

The endpoint is the one playsport's own livescore page calls::

    https://ls6.playsport.cc/ls_json.php?alliance=6&gamedate=YYYYMMDD
        &pbp=1&teamStat=1&oid=<OID>|ts,

``alliance=6`` is CPBL, ``ls7`` is an equivalent twin host, and the trailing
comma after each ``<OID>|ts`` is required -- drop it and the game is not
returned. No auth, no tokens, no CDN challenge; just be polite about it.

What comes back is keyed by playsport's *internal* game id (not the OID), each
value carrying ``official_id`` (the OID we asked for), ``r`` = [away, home]
runs as strings, and ``gs`` = the live state. ``gs.pbp`` is always empty for
CPBL -- it only populates for MLB -- so there is no event id and no play text.
That absence is why :func:`state_from_record` leans on ``GameState``'s
composite ``pitch_id`` instead of an event number.

Three defects in the raw stream, each measured, each with a fixture in
tests/fixtures/playsport_live.json, all handled by :class:`LiveGuard`:

  D1. ``gs.ss == "Final"`` carries a **corrupt away score**, mirrored from the
      home score. Game 308 truly ended 0-11; all 35 ``Final`` records said
      11-11 or 11-0, while all 250 ``結束``/``比賽結束`` records said 0-11.
      Unfiltered this fires a "tie game in the 9th" alert on a blowout.
  D2. ``gs.o >= 3`` appears transiently. CPBL never publishes 3 outs as a
      pre-pitch state and the LI table clamps outs to 2, so such a record is
      just a duplicate of the 2-out alert.
  D3. ``gs.ss in ("結束", "比賽結束")`` means the game is over: stop polling it
      for the rest of the day.

And one ordering hazard, which is what the high-water mark in
:class:`LiveGuard` is for -- see its docstring.
"""

from __future__ import annotations

import logging
import re
import time

import requests

from .models import GameState

log = logging.getLogger(__name__)

ALLIANCE_CPBL = 6
LIVESCORE_URL = f"https://www.playsport.cc/livescore/{ALLIANCE_CPBL}"
# ls6 and ls7 serve the same data; the second is a straight retry target.
FEED_HOSTS = ("https://ls6.playsport.cc", "https://ls7.playsport.cc")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# ``ss`` values. "Final" is the corrupt one (D1); the Chinese ones are the
# trustworthy end-of-game markers (D3).
STATUS_CORRUPT = "Final"
STATUS_OVER = frozenset({"結束", "比賽結束"})

_OID_RE = re.compile(r'data-oid="([^"]+)"')

# How long the scraped OID list is trusted. The livescore page only changes
# when a game starts or ends, so re-scraping an HTML page for it every 15
# seconds would be rude; never re-scraping it at all -- which is what a
# fetch-once cache amounts to -- freezes the list at the first poll of the
# day, and a game that started later never gets an id for the whole run.
OID_REFRESH_SECONDS = 180.0

# How long one batched live fetch is reused. The watcher asks about each game
# in turn within a single poll, and they should all be answered from the same
# round trip; anything longer than a poll would just be lead time thrown away.
SNAPSHOT_TTL = 3.0

# ``gs.rs`` is an ENUM INDEX, not a bitmask. Verified 91/94 against CPBL's own
# base occupancy for the same moments; reading it as a 1/2/4 bitmask scores
# 62/94, because the two disagree exactly where it matters -- 4 is 一二壘 here
# and 三壘 under a bitmask, 3 is 三壘 here and 一二壘 under a bitmask. Getting
# this backwards would put phantom runners in scoring position and alert on
# quiet innings.
BASES_BY_CODE: dict[int, tuple[bool, bool, bool]] = {
    0: (False, False, False),   # 空壘
    1: (True, False, False),    # 一
    2: (False, True, False),    # 二
    3: (False, False, True),    # 三
    4: (True, True, False),     # 一二
    5: (True, False, True),     # 一三
    6: (False, True, True),     # 二三
    7: (True, True, True),      # 滿壘
}

# playsport OIDs name the clubs by short code -- CPBL_20260904_DRAGONS@BRO_1835
# is 味全龍 at 中信兄弟, 18:35. This is only the *fallback* way to identify a
# game, because the codes are not all words: playsport also emits bare numeric
# club ids (13342 is 台鋼雄鷹), and one of the three real games recorded on
# 2026-09-04 was unresolvable this way. The primary route is the response's own
# ``aname``/``hname`` -- see :func:`clubs_match`.
TEAM_CODES: dict[str, str] = {
    "BRO": "中信兄弟",
    "DRAGONS": "味全龍",
    "UNI": "統一7-ELEVEn獅",
    "LIONS": "統一7-ELEVEn獅",
    "RAKUTEN": "樂天桃猿",
    "MONKEYS": "樂天桃猿",
    "FUBON": "富邦悍將",
    "GUARDIANS": "富邦悍將",
    "TSG": "台鋼雄鷹",
    "HAWKS": "台鋼雄鷹",
}


class PlaysportError(RuntimeError):
    pass


def _as_int(value: object, default: int = 0) -> int:
    """Coerce a feed field to int.

    Not defensive padding: playsport genuinely types the *same* field both
    ways on the same oid -- ``"o": "3"`` on one record and ``"o": 0`` on the
    next -- because two record shapes are interleaved on one stream. Comparing
    ``"3" >= 3`` is a TypeError, so every field is normalised on the way in
    and nothing downstream has to care which shape it came from.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class Record:
    """One decoded live-state sample for one game.

    Deliberately not a ``GameState``: a record can be one we intend to throw
    away (a corrupt ``Final``, a 3-out flicker), and giving it its own type
    keeps :class:`LiveGuard`'s decisions upstream of anything the alerting
    code can see.
    """

    __slots__ = ("oid", "inning", "is_top", "outs", "first", "second", "third",
                 "visiting_score", "home_score", "status", "batter", "pitcher")

    def __init__(self, oid: str, inning: int, is_top: bool, outs: int,
                 bases: tuple[bool, bool, bool], visiting_score: int,
                 home_score: int, status: str, batter: str = "",
                 pitcher: str = "") -> None:
        self.oid = oid
        self.inning = inning
        self.is_top = is_top
        self.outs = outs
        self.first, self.second, self.third = bases
        self.visiting_score = visiting_score
        self.home_score = home_score
        self.status = status
        self.batter = batter
        self.pitcher = pitcher

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return (f"<Record {self.oid} {self.inning}{'上' if self.is_top else '下'} "
                f"{self.outs}out {self.visiting_score}-{self.home_score} "
                f"ss={self.status!r}>")

    @property
    def order_key(self) -> tuple[int, int, int, int]:
        """How far into the game this sample is. Used as the high-water mark.

        Runs first, then inning, then half, then outs -- a game only ever
        moves forward through those four.

        The count is **not** in here, and must never be: balls/strikes reset
        to 0-0 on every new batter, so a tuple containing them would rank the
        *fresher* record lower and the guard would throw it away. Nothing
        downstream misses it -- ``leverage.leverage_index`` reads inning,
        half, outs, bases and score, and never the count.
        """
        return (self.visiting_score + self.home_score, self.inning,
                0 if self.is_top else 1, self.outs)

    @property
    def base_code(self) -> str:
        return "".join(d if occ else "-" for d, occ in
                       (("1", self.first), ("2", self.second), ("3", self.third)))

    @property
    def situation_id(self) -> str:
        """A stable identity for this *moment*, used as ``event_no``.

        playsport has no event number -- ``gs.pbp`` never populates for CPBL --
        so one is minted from the situation itself. Being explicit here rather
        than leaning on ``GameState.pitch_id``'s composite fallback is what
        frees the count and the names to carry their real values: the fallback
        would fold balls/strikes and the batter's name into the identity, so a
        single rally would re-alert on every pitch and again whenever the feed
        blinked a name in or out. What identifies a moment is exactly what
        ``leverage`` reads -- inning, half, outs, bases, score -- and nothing
        else belongs in it.
        """
        return (f"{self.inning}|{self.is_top}|{self.outs}|{self.base_code}|"
                f"{self.visiting_score}-{self.home_score}")

    @property
    def is_corrupt_final(self) -> bool:
        return self.status == STATUS_CORRUPT

    @property
    def is_over(self) -> bool:
        return self.status in STATUS_OVER

    @property
    def has_impossible_outs(self) -> bool:
        return self.outs >= 3


def decode_bases(rs: object) -> tuple[bool, bool, bool]:
    """Decode ``gs.rs`` into (first, second, third). See :data:`BASES_BY_CODE`."""
    return BASES_BY_CODE.get(_as_int(rs, -1), (False, False, False))


def parse_record(game: dict) -> Record | None:
    """Decode one game entry from the feed. ``None`` if there is no state.

    Accepts ``official_id`` (what the live endpoint returns) or ``oid`` (what
    the recorded fixture stores) for the game's identity, so the fixture can
    be replayed through exactly this function rather than a test-only twin.
    """
    gs = game.get("gs")
    if not isinstance(gs, dict):
        return None
    runs = game.get("r") or []
    status = gs.get("ss")
    return Record(
        oid=str(game.get("official_id") or game.get("oid") or ""),
        inning=_as_int(gs.get("i")),
        # "Y" is 上半局. Anything else -- "N", and the empty/absent case -- is
        # the bottom half, which is also the right default for a game that has
        # not thrown a pitch yet.
        is_top=str(gs.get("ti") or "").upper() == "Y",
        outs=_as_int(gs.get("o")),
        bases=decode_bases(gs.get("rs")),
        visiting_score=_as_int(runs[0] if len(runs) > 0 else 0),
        home_score=_as_int(runs[1] if len(runs) > 1 else 0),
        status="" if status is None else str(status),
        # Populated on roughly 6% of records, so these are a bonus for the
        # alert text and never part of the game's identity.
        batter=str(gs.get("br") or "").strip(),
        pitcher=str(gs.get("pr") or "").strip(),
    )


class LiveGuard:
    """Turns the raw playsport stream into a monotone, trustworthy one.

    Per oid it keeps a high-water mark on :attr:`Record.order_key` and rejects
    anything strictly behind it. The stream really does go backwards -- 22 of
    2337 samples (0.9%) did on the measured day, because two record shapes are
    interleaved and neither is systematically fresher; they leapfrog. Both
    must be consumed (dropping either loses the speed this module exists for),
    so the reordering is fixed here instead.

    Order of business matters. ``Final`` is rejected *before* the high-water
    mark is touched: its away score is mirrored from the home score, so an
    11-0 game arrives as 11-11, and letting that reach the mark would strand
    it at 22 total runs and silence the game for the rest of the night.
    """

    def __init__(self) -> None:
        self.high: dict[str, tuple[int, int, int, int]] = {}
        self.finished: set[str] = set()

    def accept(self, record: Record) -> bool:
        """Should this sample be believed and acted on?"""
        oid = record.oid
        if oid in self.finished:
            return False
        if record.is_corrupt_final:                     # D1
            log.debug("playsport %s: dropping corrupt Final (%s-%s)",
                      oid, record.visiting_score, record.home_score)
            return False
        if record.is_over:                              # D3
            log.info("playsport %s: game over (%s); no longer polling it",
                     oid, record.status)
            self.finished.add(oid)
            return False
        if record.has_impossible_outs:                  # D2
            log.debug("playsport %s: dropping %d-out flicker", oid, record.outs)
            return False

        key = record.order_key
        mark = self.high.get(oid)
        if mark is not None and key < mark:
            log.debug("playsport %s: dropping stale sample %s < %s", oid, key, mark)
            return False
        self.high[oid] = key
        return True

    def is_finished(self, oid: str) -> bool:
        return oid in self.finished

    def forget(self, oid: str) -> None:
        """Drop the high-water mark, e.g. when a game is re-attached to."""
        self.high.pop(oid, None)


def state_from_record(record: Record, meta: dict | None = None) -> GameState:
    """Build the alerting ``GameState`` from a playsport sample.

    ``event_no`` is :attr:`Record.situation_id`, not blank. ``GameState``
    returns ``event_no`` verbatim from ``pitch_id`` when it is truthy, so
    stating the identity outright takes it out of the hands of the composite
    fallback -- which would fold in balls/strikes and the batter's name and
    re-alert the same rally on every pitch. ``dedupe.py`` is untouched either
    way; the difference is that the count and the names are now free to be
    *shown*, which is the whole reason to spend a field on this.

    The count is still zero: playsport's ``gs.b``/``gs.s`` are there, but the
    alert does not print a count and a number that nothing reads is a number
    that can only be wrong.
    """
    meta = meta or {}
    return GameState(
        game_sno=int(meta.get("GameSno") or 0),
        year=str(meta.get("Year") or ""),
        kind_code=str(meta.get("KindCode") or "A"),
        inning=record.inning,
        is_top=record.is_top,
        outs=record.outs,
        first=record.first,
        second=record.second,
        third=record.third,
        balls=0,
        strikes=0,
        visiting_score=record.visiting_score,
        home_score=record.home_score,
        batter=record.batter,
        pitcher=record.pitcher,
        event_no=record.situation_id,
        created_at="",
        visiting_team=str(meta.get("VisitingTeamName") or ""),
        home_team=str(meta.get("HomeTeamName") or ""),
    )


def clubs_match(short: str, full: str) -> bool:
    """Does playsport's club name mean the same club as CPBL's?

    playsport prints the short name -- 雄鷹, 兄弟, 龍, 獅, 猿, 富邦 -- where
    CPBL prints the sponsored full one: 台鋼雄鷹, 中信兄弟, 統一7-ELEVEn獅.
    Containment either way spans that without a table to maintain, and a table
    is exactly what went wrong before: the OID's club *codes* include bare
    numeric ids (13342), so a third of one night's games could not be
    identified at all.

    Loose on its own -- 獅 is one character -- which is why the caller
    requires *both* clubs of a game to match. Matching the wrong game would
    push another stadium's rally to the phone, so this is the one place here
    that must not be clever.
    """
    short, full = str(short or "").strip(), str(full or "").strip()
    if not short or not full:
        return False
    return short in full or full in short


def entry_clubs(entry: dict) -> tuple[str, str]:
    """(away, home) club names as the live response gives them."""
    return str(entry.get("aname") or "").strip(), str(entry.get("hname") or "").strip()


def oid_teams(oid: str) -> tuple[str, str] | None:
    """(visiting, home) CPBL team names from the OID string alone.

    The *fallback* identifier, used only when a live entry carries no
    ``aname``/``hname`` to match on. ``CPBL_20260904_DRAGONS@BRO_1835`` ->
    ('味全龍', '中信兄弟'). Both codes must resolve: half a match is not a
    match, and a wrong one would push another game's situation to the phone.
    """
    parts = oid.split("_")
    if len(parts) < 3 or "@" not in parts[2]:
        return None
    away, _, home = parts[2].partition("@")
    visiting = TEAM_CODES.get(away.upper())
    hosting = TEAM_CODES.get(home.upper())
    if not visiting or not hosting:
        return None
    return visiting, hosting


class PlaysportClient:
    """Session-aware client for the playsport live feed.

    Constructing one touches the network exactly never -- the session and the
    per-day OID list are both built lazily. That matters because the watcher
    is constructed at startup by the CLI, including in ``--once`` runs and in
    tests, and a source that dials out just to exist would be the wrong kind
    of eager.
    """

    def __init__(self, timeout: int = 10, min_interval: float = 1.0,
                 oid_ttl: float = OID_REFRESH_SECONDS) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self.oid_ttl = oid_ttl
        self._last_request = 0.0
        self._session: requests.Session | None = None
        self._oid_cache: tuple[str, list[str]] | None = None
        self._oid_fetched_at = 0.0
        # Injectable so the expiry can be tested without sleeping through it.
        self._clock = time.monotonic

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.headers.update({
                "User-Agent": UA,
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "zh-TW,zh;q=0.9",
                "Referer": LIVESCORE_URL,
            })
            self._session = s
        return self._session

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.monotonic()

    def oids(self, gamedate: str, refresh: bool = False) -> list[str]:
        """Today's OIDs, scraped from the livescore page and cached briefly.

        The page lists only games that have not finished, so the set really
        does change during an evening -- a 21:00 game is simply not on it at
        18:40. Caching it *forever*, which is what fetch-once amounts to, is
        therefore not a cache but a bug: the second game of the night would
        never get an id and would silently spend the whole run on the slower
        official feed. So the list expires after :data:`OID_REFRESH_SECONDS`,
        and :class:`PlaysportSource` also asks for ``refresh=True`` the moment
        a game it is watching has no id -- which is the case that matters and
        the one a timer alone would answer minutes late.

        Still cached at all because this is an HTML page fetched for a handful
        of attributes, and it cannot change between two 15-second polls.
        """
        fresh = (self._oid_cache and self._oid_cache[0] == gamedate
                 and self._clock() - self._oid_fetched_at < self.oid_ttl)
        if not refresh and fresh:
            return self._oid_cache[1]
        self._throttle()
        resp = self.session.get(LIVESCORE_URL, timeout=self.timeout)
        resp.raise_for_status()
        prefix = f"CPBL_{gamedate}_"
        found = [o for o in dict.fromkeys(_OID_RE.findall(resp.text))
                 if o.startswith(prefix)]
        self._oid_cache = (gamedate, found)
        self._oid_fetched_at = self._clock()
        return found

    def live(self, gamedate: str, oids: list[str]) -> list[dict]:
        """Fetch the live state for ``oids``. Returns the game entries.

        The ``timestamp`` key in the response is the epoch-ms of the *request*,
        not of the data, so it is dropped here rather than tempting anyone
        downstream into using it as a freshness stamp.
        """
        if not oids:
            return []
        # The trailing comma on each entry is not a typo; the endpoint parses
        # the oid list by it and returns nothing for the last game without it.
        oid_param = "".join(f"{oid}|ts," for oid in oids)
        params = {"alliance": str(ALLIANCE_CPBL), "gamedate": gamedate,
                  "pbp": "1", "teamStat": "1", "oid": oid_param}
        last: Exception | None = None
        for host in FEED_HOSTS:
            self._throttle()
            try:
                resp = self.session.get(f"{host}/ls_json.php", params=params,
                                        timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.debug("playsport %s failed: %s", host, exc)
                last = exc
                continue
            if not isinstance(data, dict):
                last = PlaysportError("ls_json returned a non-object")
                continue
            return [v for k, v in data.items()
                    if k != "timestamp" and isinstance(v, dict)]
        raise PlaysportError(f"playsport feed unavailable: {last}")


class PlaysportSource:
    """The watcher's view of playsport: one current ``GameState`` per game.

    Per CPBL game it answers two separate questions, and keeping them separate
    is the whole point:

      * :meth:`available` -- can this game use playsport at all? (Is there an
        OID for it, and has it not failed too often?) This is what the watcher
        picks a source with.
      * :meth:`state_for` -- what is happening right now? ``None`` here means
        "nothing new this poll", which is ordinary and must **not** be read as
        "playsport is broken"; treating it that way would flip the game back
        to the official feed and reset its tracker on every quiet poll.

    A game that fails ``max_failures`` times in a row is demoted to the
    official feed and stays there for the rest of the run. Not re-promoting is
    deliberate: every switch costs a tracker reset, and flapping between two
    sources with two different pitch-id schemes is a worse failure than losing
    a minute of lead time on one game.
    """

    def __init__(self, client: PlaysportClient | None = None,
                 max_failures: int = 3, snapshot_ttl: float = SNAPSHOT_TTL) -> None:
        self.client = client or PlaysportClient()
        self.max_failures = max_failures
        self.snapshot_ttl = snapshot_ttl
        self.guard = LiveGuard()
        self._entries: dict[str, list[dict]] = {}   # oid -> this poll's samples
        self._snapshot_day = ""
        self._snapshot_at = 0.0
        self._failures = 0
        self.demoted = False
        self._unmatched_logged: set[str] = set()
        self._last_forced = 0.0
        self._clock = time.monotonic

    # -- the poll ----------------------------------------------------------
    def snapshot(self, gameday: str, refresh: bool = False) -> dict[str, list[dict]]:
        """This poll's live entries, keyed by OID. One round trip for all games.

        The endpoint takes an OID *list* and answers them together, so asking
        it once per game would spend the throttle -- and the lead time this
        module exists for -- on nothing. The result is held for
        ``snapshot_ttl`` seconds, which is long enough for the watcher's loop
        over tonight's games and far too short to serve two polls.
        """
        fresh = (self._snapshot_day == gameday
                 and self._clock() - self._snapshot_at < self.snapshot_ttl)
        if not refresh and fresh:
            return self._entries

        gamedate = gameday.replace("-", "")
        try:
            oids = [o for o in self.client.oids(gamedate, refresh=refresh)
                    if not self.guard.is_finished(o)]
            entries = self.client.live(gamedate, oids) if oids else []
        except (requests.RequestException, PlaysportError, ValueError) as exc:
            self._note_failure(exc)
            return self._entries

        grouped: dict[str, list[dict]] = {}
        for entry in entries:
            oid = str(entry.get("official_id") or entry.get("oid") or "")
            if oid:
                grouped.setdefault(oid, []).append(entry)
        self._entries = grouped
        self._snapshot_day = gameday
        self._snapshot_at = self._clock()
        self._failures = 0
        return grouped

    # -- matching ----------------------------------------------------------
    def oid_for(self, game: dict, gameday: str) -> str | None:
        """The OID for a CPBL game, or ``None`` if we cannot identify it.

        Matched on the club names the live response itself carries
        (``aname``/``hname``), not on the OID's club codes: those codes are
        sometimes bare numeric ids, and on the day this was measured that was
        one game in three quietly getting no speedup at all. The OID codes
        remain as a fallback for an entry that carries no names.

        Not matched on time. CPBL routinely starts every game of a day at
        18:35, so the OID's time suffix does not discriminate between them.

        A game we cannot identify is re-looked-up against a freshly scraped
        OID list before giving up -- the usual reason is simply that it had
        not started when the list was last fetched -- and then said so out
        loud, once, at INFO. Silence here is what made this fail invisibly
        before: the official-feed fallback works, so nothing looks wrong.
        """
        visiting = str(game.get("VisitingTeamName") or "")
        home = str(game.get("HomeTeamName") or "")
        if not visiting or not home:
            return None

        entries = self.snapshot(gameday)
        oid = self._find(entries, visiting, home)
        if oid is None and self._may_force():
            self._last_forced = self._clock()
            entries = self.snapshot(gameday, refresh=True)
            oid = self._find(entries, visiting, home)
        if oid is None:
            # An empty snapshot is not a failure to match: the feed carries
            # nothing at all for a game that has not thrown a pitch yet, so
            # before first pitch *every* game looks unmatched. Announcing
            # "stays on the official feed" then would be both alarming and
            # wrong -- the game moves over to playsport as soon as it starts,
            # because ``source_for`` re-decides every cycle. Only a game
            # missing from a snapshot that does carry other games is genuinely
            # unidentifiable, and that is the one worth saying out loud.
            key = f"{visiting}@{home}"
            if not entries:
                log.debug("playsport has no live entries yet; %s vs %s waits",
                          visiting, home)
            elif key not in self._unmatched_logged:
                self._unmatched_logged.add(key)
                log.info("playsport has no game matching %s vs %s; "
                         "that game stays on the official CPBL feed", visiting, home)
            return None
        self._unmatched_logged.discard(f"{visiting}@{home}")
        return oid

    def _may_force(self) -> bool:
        """Rate-limit the on-demand re-scrape.

        An unmatched game is unmatched on every poll, so without this the
        "look again, it may have started" retry would re-scrape the livescore
        page every 15 seconds for the rest of the night on behalf of a game
        that is never coming.
        """
        return self._clock() - self._last_forced >= self.client.oid_ttl

    @staticmethod
    def _find(entries: dict[str, list[dict]], visiting: str, home: str) -> str | None:
        """The OID whose two clubs are this game's two clubs, or ``None``."""
        for oid, samples in entries.items():
            for entry in samples:
                away_name, home_name = entry_clubs(entry)
                if away_name and home_name:
                    if (clubs_match(away_name, visiting)
                            and clubs_match(home_name, home)):
                        return oid
                    continue
                teams = oid_teams(oid)          # fallback: the OID's own codes
                if teams and teams[0] == visiting and teams[1] == home:
                    return oid
        return None

    # -- source selection --------------------------------------------------
    def available(self, game: dict, gameday: str) -> bool:
        """Whether this game should be served from playsport at all."""
        if self.demoted:
            return False
        return bool(self.oid_for(game, gameday))

    # -- state -------------------------------------------------------------
    def state_for(self, game: dict, gameday: str) -> GameState | None:
        """The current situation for one game, or ``None`` if there is none.

        ``None`` covers every ordinary quiet case: the game is over, every
        sample this poll was a defect, or nothing has moved since last time.
        Only repeated *errors* mean playsport itself is in trouble, and those
        are counted in :meth:`snapshot`.
        """
        if self.demoted:
            return None
        oid = self.oid_for(game, gameday)
        if not oid or self.guard.is_finished(oid):
            return None
        entries = self.snapshot(gameday).get(oid) or []

        # Consume *both* record shapes, oldest-looking first, so the guard's
        # high-water mark ends up on the furthest-advanced sample rather than
        # on whichever one the server happened to list first.
        records = [r for r in (parse_record(e) for e in entries) if r is not None]
        best: Record | None = None
        for record in sorted(records, key=lambda r: r.order_key):
            record.oid = record.oid or oid
            if self.guard.accept(record):
                best = record
        if best is None:
            return None
        return state_from_record(best, game)

    def _note_failure(self, exc: Exception) -> None:
        """Count a failed fetch; demote every watched game once it is chronic.

        The fetch is batched, so a failure is not attributable to one game --
        it is the feed being down. Demotion is therefore all-or-nothing, and
        permanent for the run: every switch back and forth costs a tracker
        reset, and flapping between two pitch-id schemes is a worse failure
        than losing a minute of lead time.
        """
        self._failures += 1
        log.warning("playsport fetch failed (%d/%d): %s",
                    self._failures, self.max_failures, exc)
        if self._failures >= self.max_failures:
            log.warning("playsport demoted; falling back to the official CPBL feed")
            self.demoted = True

    def release(self, game: dict, gameday: str) -> None:
        """Forget a game's high-water mark (used when it changes source)."""
        oid = self.oid_for(game, gameday)
        if oid:
            self.guard.forget(oid)
