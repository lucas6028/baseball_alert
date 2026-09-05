"""The polling loop: discover live games, watch them, fire alerts."""

from __future__ import annotations

import datetime as dt
import logging
import time

from .client import MIN_POLL_SECONDS, CpblClient
from .dedupe import GameTracker
from .leverage import DEFAULT_THRESHOLD, assess
from .models import state_from_row
from .notifier import Notifier, format_alert
from .playsport import PlaysportSource

log = logging.getLogger(__name__)

# CPBL games start late afternoon Taiwan time; outside that window there is
# nothing to watch, so idle cheaply instead of hammering the site.
ACTIVE_HOURS = (16, 24)
IDLE_SLEEP = 300

TAIPEI = dt.timezone(dt.timedelta(hours=8))


def now_tw() -> dt.datetime:
    """Current time in Taiwan, independent of this machine's timezone."""
    return dt.datetime.now(TAIPEI)


def today_tw() -> str:
    return now_tw().strftime("%Y-%m-%d")


class Watcher:
    def __init__(
        self,
        client: CpblClient,
        notifier: Notifier,
        threshold: float = DEFAULT_THRESHOLD,
        poll_seconds: int = 15,
        teams: list[str] | None = None,
        dry_run: bool = False,
        cpbl_source: str = "cpbl",
        playsport: PlaysportSource | None = None,
    ) -> None:
        self.client = client
        self.notifier = notifier
        self.threshold = threshold
        self.poll_seconds = max(poll_seconds, MIN_POLL_SECONDS)
        self.teams = [t for t in (teams or []) if t]
        self.dry_run = dry_run
        self.trackers: dict[int, GameTracker] = {}
        # Which feed serves each game. "playsport" is 33-119 seconds ahead of
        # the official live log (which is rebuilt server-side only every
        # 70-110s), so it is the default in config; "cpbl" forces the original
        # path. The default *here* stays "cpbl" so that constructing a Watcher
        # directly -- which is what every existing caller and test does --
        # behaves exactly as it always did. The CLI passes the config value.
        self.cpbl_source = cpbl_source
        self.playsport = playsport or (PlaysportSource()
                                       if cpbl_source == "playsport" else None)
        self.sources: dict[int, str] = {}

    # -- helpers -----------------------------------------------------------
    def interested(self, meta: dict) -> bool:
        """If the user named teams, only watch games involving them.

        This is a whole-game decision, so it is made from the game metadata
        before any row is fed to the tracker -- feeding rows through
        ``should_fire`` first would burn the tracker's rally state on a game
        we are about to discard.
        """
        if not self.teams:
            return True
        names = f"{meta.get('VisitingTeamName', '')}{meta.get('HomeTeamName', '')}"
        return any(t in names for t in self.teams)

    def _tracker(self, game_sno: int) -> GameTracker:
        return self.trackers.setdefault(game_sno, GameTracker())

    @staticmethod
    def _half(state) -> tuple:
        """The half-inning, ordered so that later halves compare greater.

        ``not is_top`` rather than ``is_top``: the bottom of the 6th comes
        after the top of it, and ``True > False`` would order those backwards.
        """
        return (state.inning, not state.is_top)

    # -- core --------------------------------------------------------------
    def process(self, payload: dict, game_sno: int | str) -> int:
        """Run one live payload through the model. Returns alerts fired."""
        rows, meta = payload.get("rows") or [], payload.get("meta") or {}
        if not rows or not self.interested(meta):
            return 0

        tracker = self._tracker(int(game_sno))
        fired = 0
        states = [state_from_row(row, meta) for row in rows]
        # The half-inning the game is actually in. Everything before it is
        # history: even if the watermark somehow lost track of a row, a 四上
        # bases-loaded alert delivered while the game is already in 五下 is
        # wrong -- the premise of this thing is "turn the TV on *now*". The
        # cost is that a rally which both started and ended between two polls
        # goes unannounced, which is the right trade: it is over either way.
        #
        # Taken as the max rather than from the last row. The log is ordered
        # in practice (checked on a finished game and a live one), but it also
        # carries special-event rows -- 比賽結束 is one -- and a stray one at
        # the tail must not be able to silence a live rally.
        live_half = max(self._half(st) for st in states)
        # Walk every row so a burst between polls is never missed; the
        # pitch-id watermark inside the tracker makes replaying old rows a
        # no-op.
        for state in states:
            assessment = assess(state, threshold=self.threshold)
            if not tracker.should_fire(state, assessment):
                continue
            if self._half(state) != live_half:
                log.debug("game %s: not alerting on history (%s, live half is %s)",
                          game_sno, state.describe(), live_half)
                continue
            self._emit(state, assessment, game_sno)
            fired += 1
        return fired

    def _emit(self, state, assessment, game_sno) -> None:
        """Deliver one alert. Shared by both feeds so they cannot drift."""
        text = format_alert(state, assessment)
        log.info("ALERT game %s | %s | LI=%.2f",
                 game_sno, state.describe(), assessment.leverage)
        if self.dry_run:
            print(text)
        else:
            self.notifier.send(text)

    def process_state(self, state, game_sno: int | str,
                      meta: dict | None = None) -> int:
        """Run a single already-current state through the model.

        The playsport counterpart to :meth:`process`. There is no history walk
        and no live-half check because there is no history: playsport gives
        one sample, and it is by construction the situation the game is in
        right now. Everything else -- the team filter, the tracker, the alert
        text -- is the same code, keyed on the same ``int(game_sno)``, so the
        two feeds cannot fork a game's memory between them.
        """
        if not self.interested(meta or {}):
            return 0
        tracker = self._tracker(int(game_sno))
        assessment = assess(state, threshold=self.threshold)
        if not tracker.should_fire(state, assessment):
            return 0
        self._emit(state, assessment, game_sno)
        return 1

    def prime(self, payload: dict, game_sno: int | str) -> None:
        """Mark everything already played as seen, so startup doesn't spam.

        Without this, attaching to a game in the 7th inning would replay every
        earlier rally as a burst of stale alerts.
        """
        tracker = self._tracker(int(game_sno))
        for row in payload.get("rows") or []:
            tracker.seen_pitches.add(state_from_row(row).pitch_id)
        log.info("primed game %s with %d existing pitches",
                 game_sno, len(tracker.seen_pitches))

    def prime_playsport(self, game: dict, game_sno: int | str, gameday: str) -> None:
        """Mark playsport's *current* situation as already seen.

        :meth:`prime` marks the official feed's ``MainEventNo`` ids seen, which
        says nothing about playsport: that feed has no event id, so its states
        are identified by the composite situation key instead (see
        :func:`cpbl_alert.playsport.state_from_record`). Two id namespaces, no
        overlap. Without this, attaching to -- or switching to -- playsport
        mid-rally re-announces the moment the other feed already announced.
        """
        if self.playsport is None:
            return
        try:
            state = self.playsport.state_for(game, gameday)
        except Exception as exc:  # noqa: BLE001 - priming must never be fatal
            log.debug("game %s: playsport priming failed: %s", game_sno, exc)
            return
        if state is not None:
            self._tracker(int(game_sno)).seen_pitches.add(state.pitch_id)

    def source_for(self, game: dict, game_sno: int | str, gameday: str,
                   payload: dict | None = None) -> str:
        """Which feed serves this game -- decided per game, and sticky.

        Sticky because the two feeds mint pitch ids in two different schemes:
        the official log uses ``MainEventNo``, playsport uses the composite
        situation key. A tracker's memory of one means nothing to the other,
        so a game served by both in the same run would push the same rally
        twice. Hence exactly one feed per game at a time, and a change of feed
        wipes that game's tracker and re-primes it in the new scheme before
        anything is allowed to alert.
        """
        key = int(game_sno)
        if self.cpbl_source != "playsport" or self.playsport is None:
            return "cpbl"
        try:
            chosen = "playsport" if self.playsport.available(game, gameday) else "cpbl"
        except Exception as exc:  # noqa: BLE001
            log.debug("game %s: playsport availability check failed: %s", game_sno, exc)
            chosen = "cpbl"

        previous = self.sources.get(key)
        self.sources[key] = chosen
        if previous is None or previous == chosen:
            return chosen

        log.info("game %s: switching from the %s feed to the %s feed",
                 game_sno, previous, chosen)
        self.trackers.pop(key, None)
        if payload:
            self.prime(payload, game_sno)
        if chosen == "playsport":
            self.playsport.release(game, gameday)
            self.prime_playsport(game, game_sno, gameday)
        return chosen

    def check_game(self, game_sno: int | str, year: str, kind_code: str = "A") -> int:
        """Fetch and process one game (used for manual/one-off checks)."""
        return self.process(self.client.game_live(game_sno, year, kind_code), game_sno)

    # -- main loop ---------------------------------------------------------
    def run(self, once: bool = False) -> None:
        primed: set[int] = set()
        while True:
            gameday = today_tw()
            hour = now_tw().hour
            try:
                live = self.client.live_games(gameday)
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                log.warning("schedule lookup failed: %s", exc)
                live = []

            if not live:
                if once:
                    log.info("no live games right now")
                    return
                sleep = (self.poll_seconds if ACTIVE_HOURS[0] <= hour < ACTIVE_HOURS[1]
                         else IDLE_SLEEP)
                log.debug("no live games; sleeping %ss", sleep)
                time.sleep(sleep)
                continue

            for game in live:
                sno = game.get("GameSno")
                if sno is None:
                    continue
                # live_games already fetched this payload -- don't refetch it.
                payload = game.get("_payload")
                try:
                    if payload is None:
                        payload = self.client.game_live(
                            sno, str(game.get("Year") or gameday[:4]),
                            str(game.get("KindCode") or "A"))
                    # The schedule row for ids and times, the live payload's
                    # meta on top for the team names. Both carry GameSno/Year/
                    # KindCode identically, and meta is the authoritative one
                    # -- it is what every other team-name reader in this file
                    # is already fed, so the playsport path cannot end up
                    # matching games by a key the rest of the code does not use.
                    context = {**game, **(payload.get("meta") or {})}
                    # Decided before priming, so the first attach primes in
                    # whichever feed's id scheme is about to be used.
                    source = self.source_for(context, sno, gameday, payload)
                    if int(sno) not in primed:
                        self.prime(payload, sno)
                        if source == "playsport":
                            self.prime_playsport(context, sno, gameday)
                        primed.add(int(sno))
                        continue
                    if source == "playsport":
                        state = self.playsport.state_for(context, gameday)
                        # None is the ordinary quiet case -- nothing moved, or
                        # every sample this poll was a known defect. It is not
                        # a reason to fall back; only repeated *errors* are,
                        # and PlaysportSource counts those itself.
                        if state is not None:
                            self.process_state(state, sno, context)
                        continue
                    self.process(payload, sno)
                except Exception as exc:  # noqa: BLE001
                    log.warning("game %s poll failed: %s", sno, exc)

            if once:
                return
            time.sleep(self.poll_seconds)
