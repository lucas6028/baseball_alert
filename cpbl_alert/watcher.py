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
    ) -> None:
        self.client = client
        self.notifier = notifier
        self.threshold = threshold
        self.poll_seconds = max(poll_seconds, MIN_POLL_SECONDS)
        self.teams = [t for t in (teams or []) if t]
        self.dry_run = dry_run
        self.trackers: dict[int, GameTracker] = {}

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
            text = format_alert(state, assessment)
            log.info("ALERT game %s | %s | LI=%.2f",
                     game_sno, state.describe(), assessment.leverage)
            if self.dry_run:
                print(text)
            else:
                self.notifier.send(text)
            fired += 1
        return fired

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
                    if int(sno) not in primed:
                        self.prime(payload, sno)
                        primed.add(int(sno))
                        continue
                    self.process(payload, sno)
                except Exception as exc:  # noqa: BLE001
                    log.warning("game %s poll failed: %s", sno, exc)

            if once:
                return
            time.sleep(self.poll_seconds)
