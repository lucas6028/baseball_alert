"""MLB: buzz when a Taiwanese player is the one on stage.

The trigger here is not tension, it is *identity*. 心跳指數 answers "is this
worth turning the TV on for"; this answers a different question -- "is he up
right now" -- and the answer is binary, so there is no score to compute and
no threshold to tune. What the two share is the phone: the same four-line
budget, the same diamond, the same bot.

Three things about MLB's StatsAPI shaped this file:

  1. **One request covers the whole league.** ``/schedule?hydrate=linescore``
     returns every game's current batter, pitcher, bases, outs, count and
     score in a single ~50KB payload. So the poller makes *one* call per
     tick no matter how many games are on, which is why it can afford to
     watch all fifteen of them. Fetching each game's ``feed/live`` instead
     would be 500KB apiece.

  2. **Nationality is in the data.** ``/sports/1/players`` carries
     ``birthCountry``, and MLB spells Taiwan "Taiwan" -- so who counts is a
     lookup, not a hand-maintained list. The hand-maintained list still
     exists (:data:`TAIWANESE_NAMES`), but as a *backstop* for a call-up the
     roster endpoint has not caught up with, and as the source of Chinese
     names, which the API does not have at all.

  3. **The date is a US business date.** A 20:15 ET game on Aug 28 is listed
     under ``2026-08-28`` even though it starts at 00:15 UTC on the 29th --
     and at 08:15 the next morning in Taiwan, which is when you would be
     watching it. So the window is always two days wide and every ``dates``
     entry is flattened together; taking ``dates[0]`` drops half the night.

Unlike CPBL there is no scraping, no anti-forgery token and no CDN
challenge: this is a real public JSON API. Be polite anyway.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import urllib.parse

import requests

from .models import GameState
from .notifier import Notifier, cn_number, format_stage_alert
from .stage import Spotlight, Stage, arrivals

log = logging.getLogger(__name__)

BASE = "https://statsapi.mlb.com/api/v1"
MLB_SPORT_ID = 1

# birthCountry, exactly as MLB spells it.
TAIWAN = "Taiwan"

MIN_POLL_SECONDS = 10
IDLE_SLEEP = 300
# How close to first pitch we stop idling and start polling properly.
WARMUP_MINUTES = 30

# Chinese names for Taiwanese players, keyed by MLB person id where it has
# been verified against the API, by ``fullName`` otherwise. This dict does
# double duty: it is what puts 鄧愷威 rather than "Kai-Wei Teng" on the lock
# screen, and it is the fallback detector for anyone the roster endpoint has
# not listed yet.
#
# A wrong Chinese name here ships to someone's phone as a wrong name, so it
# holds only players whose characters are not in doubt; anyone else falls
# through to the English name the API gave us, which is a blemish rather
# than a bug.
TAIWANESE_IDS: dict[int, str] = {
    691907: "鄭宗哲",     # Tsung-Che Cheng
    701678: "李灝宇",     # Hao-Yu Lee
    678906: "鄧愷威",     # Kai-Wei Teng
}

TAIWANESE_NAMES: dict[str, str] = {
    "Chien-Ming Wang": "王建民",
    "Hong-Chih Kuo": "郭泓志",
    "Chin-hui Tsao": "曹錦輝",
    "Chin-Feng Chen": "陳金鋒",
    "Fu-Te Ni": "倪福德",
    "Wei-Yin Chen": "陳偉殷",
    "Chia-Jen Lo": "羅嘉仁",
    "Wei-Chung Wang": "王維中",
    "Che-Hsuan Lin": "林哲瑄",
    "Tzu-Wei Lin": "林子偉",
    "Chih-Wei Hu": "胡智為",
    "Jen-Ho Tseng": "曾仁和",
    "Wei-Chieh Huang": "黃暐傑",
    "Yu Chang": "張育成",
    "Tsung-Che Cheng": "鄭宗哲",
    "Hao-Yu Lee": "李灝宇",
    "Kai-Wei Teng": "鄧愷威",
}

# Team names, keyed by team id rather than by name. Ids are permanent; names
# are not -- Cleveland and the Athletics both changed inside a decade, and a
# rename would silently put "Los Angeles Dodgers" (19 of line one's 38
# columns) back on the phone. The fallback is the team's short ``teamName``
# ("Dodgers"), which costs 7 columns instead of 19.
MLB_TEAMS: dict[int, str] = {
    108: "天使", 109: "響尾蛇", 110: "金鶯", 111: "紅襪", 112: "小熊",
    113: "紅人", 114: "守護者", 115: "落磯", 116: "老虎", 117: "太空人",
    118: "皇家", 119: "道奇", 120: "國民", 121: "大都會", 133: "運動家",
    134: "海盜", 135: "教士", 136: "水手", 137: "巨人", 138: "紅雀",
    139: "光芒", 140: "遊騎兵", 141: "藍鳥", 142: "雙城", 143: "費城人",
    144: "勇士", 145: "白襪", 146: "馬林魚", 147: "洋基", 158: "釀酒人",
}

_SUFFIXES = frozenset({"Jr.", "Sr.", "II", "III", "IV"})


class MlbError(RuntimeError):
    pass


def surname(full_name: str) -> str:
    """'Tarik Skubal' -> 'Skubal'. What a scoreboard shows, and it fits.

    A generational suffix belongs to the surname ('Luis Garcia Jr.'), so it
    comes along; anything else in the middle does not.
    """
    parts = (full_name or "").split()
    if not parts:
        return ""
    if len(parts) > 2 and parts[-1] in _SUFFIXES:
        return " ".join(parts[-2:])
    return parts[-1]


def display_name(player: dict | None) -> str:
    """The name to print: Chinese if we know it, else the surname."""
    if not player:
        return ""
    full = str(player.get("fullName") or "")
    zh = TAIWANESE_IDS.get(_person_id(player) or 0) or TAIWANESE_NAMES.get(full)
    return zh or surname(full)


def team_name(side: dict) -> str:
    """Chinese short name for a schedule game's home/away side."""
    info = side.get("team") or {}
    try:
        team_id = int(info.get("id"))
    except (TypeError, ValueError):
        team_id = 0
    return (MLB_TEAMS.get(team_id)
            or str(info.get("teamName") or info.get("name") or ""))


def _person_id(player: dict | None) -> int | None:
    try:
        return int((player or {}).get("id"))
    except (TypeError, ValueError):
        return None


def state_from_mlb_game(game: dict) -> GameState | None:
    """One live schedule game -> the shared :class:`GameState`.

    Returns ``None`` when the game is not showing a situation worth reading:
    before first pitch, and during the swap between half-innings. The swap
    matters -- with ``outs == 3`` the log still names a batter and a pitcher,
    but they are the pair from the half that just ended mixed with the one
    about to start, and firing on that pairing would put a matchup on the
    phone that never happens. The next poll, twenty seconds later, sees the
    real one.
    """
    line = game.get("linescore") or {}
    inning = int(line.get("currentInning") or 0)
    outs = int(line.get("outs") or 0)
    if not inning or outs >= 3:
        return None

    offense = line.get("offense") or {}
    defense = line.get("defense") or {}
    teams = game.get("teams") or {}
    away, home = teams.get("away") or {}, teams.get("home") or {}
    runs = line.get("teams") or {}

    def score(which: str, side: dict) -> int:
        got = (runs.get(which) or {}).get("runs")
        return int(got if got is not None else (side.get("score") or 0))

    return GameState(
        game_sno=int(game.get("gamePk") or 0),
        year=str(game.get("season") or str(game.get("gameDate") or "")[:4]),
        kind_code=str(game.get("gameType") or ""),
        inning=inning,
        is_top=bool(line.get("isTopInning")),
        outs=outs,
        # An unoccupied base is absent from the payload rather than empty,
        # so this is a presence test, not a value test.
        first=bool(offense.get("first")),
        second=bool(offense.get("second")),
        third=bool(offense.get("third")),
        balls=int(line.get("balls") or 0),
        strikes=int(line.get("strikes") or 0),
        visiting_score=score("away", away),
        home_score=score("home", home),
        # The man at the plate is on the offense; the man on the mound is on
        # the DEFENSE. ``offense.pitcher`` also exists -- it is the batting
        # team's own pitcher of record, and reading it here would name the
        # wrong man in every alert.
        batter=display_name(offense.get("batter")),
        pitcher=display_name(defense.get("pitcher")),
        event_no="",
        created_at="",
        visiting_team=team_name(away),
        home_team=team_name(home),
    )


# -- client ----------------------------------------------------------------
class MlbClient:
    """Thin StatsAPI client. Reuse one instance for the life of the poller."""

    def __init__(self, timeout: int = 20, min_interval: float = 1.0) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        self._roster: tuple[str, dict[int, str]] | None = None
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "cpbl-alert (personal notification tool)",
        })

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        url = f"{BASE}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as exc:
            raise MlbError(f"{path} returned non-JSON") from exc

    # -- schedule ----------------------------------------------------------
    def schedule(self, start: str, end: str) -> list[dict]:
        """Every game between two US business dates, flattened.

        ``dates`` comes back one entry per date and a night game lands on the
        date it *started* in the US, so both entries have to be walked -- see
        the module docstring.
        """
        data = self._get("/schedule", {
            "sportId": MLB_SPORT_ID,
            "startDate": start,
            "endDate": end,
            "hydrate": "linescore",
        })
        return [g for date in data.get("dates") or []
                for g in date.get("games") or []]

    @staticmethod
    def live(games: list[dict]) -> list[dict]:
        """Only games actually in progress.

        A finished game's linescore still names the last batter and pitcher,
        so leaving one in would let a nine-innings-ago matchup look like a
        new arrival at the plate.
        """
        return [g for g in games
                if ((g.get("status") or {}).get("abstractGameState")) == "Live"]

    def boxscore(self, game_pk: int | str) -> dict:
        """Today's stat lines for one game. Only fetched when an alert fires.

        Trimmed with ``fields`` -- the full boxscore is 160KB and we want
        four numbers out of it.
        """
        return self._get(f"/game/{game_pk}/boxscore", {
            "fields": ",".join((
                "teams", "away", "home", "players", "person", "id", "fullName",
                "stats", "batting", "pitching", "atBats", "hits",
                "homeRuns", "rbi", "plateAppearances", "inningsPitched",
                "strikeOuts", "earnedRuns", "battersFaced",
            )),
        })

    # -- who counts as Taiwanese -------------------------------------------
    def taiwanese_players(self, season: str, gameday: str = "") -> dict[int, str]:
        """``{person id: fullName}`` for every Taiwanese player in MLB.

        One request for the whole league, cached for the game-day: a roster
        does not change often enough to re-fetch every twenty seconds, and a
        call-up that lands between refreshes is what
        :data:`TAIWANESE_NAMES` is for.
        """
        key = f"{season}:{gameday}"
        if self._roster and self._roster[0] == key:
            return self._roster[1]
        data = self._get(f"/sports/{MLB_SPORT_ID}/players", {
            "season": season,
            "fields": "people,id,fullName,birthCountry",
        })
        found = {int(p["id"]): str(p.get("fullName") or "")
                 for p in data.get("people") or []
                 if p.get("birthCountry") == TAIWAN and p.get("id")}
        self._roster = (key, found)
        log.info("MLB roster: %d Taiwanese player(s) in the %s season",
                 len(found), season)
        return found


# -- stat lines ------------------------------------------------------------
def batting_detail(stats: dict) -> str:
    """'今日 1-2・1轟' -- the day so far, or '' before his first trip up."""
    if not int(stats.get("plateAppearances") or 0):
        return ""
    hits, at_bats = int(stats.get("hits") or 0), int(stats.get("atBats") or 0)
    parts = [f"今日 {hits}-{at_bats}"]
    homers, rbi = int(stats.get("homeRuns") or 0), int(stats.get("rbi") or 0)
    # One extra fact at most: the home run outranks the RBI it already
    # implies, and a third clause would push the line past the budget.
    if homers:
        parts.append(f"{homers}轟")
    elif rbi:
        parts.append(f"{rbi}打點")
    return "・".join(parts)


def pitching_detail(stats: dict) -> str:
    """'今日 4.2局・7K・失3' -- '' for a reliever who has not thrown yet."""
    if not int(stats.get("battersFaced") or 0):
        return ""
    innings = str(stats.get("inningsPitched") or "0.0")
    parts = [f"今日 {innings}局", f"{int(stats.get('strikeOuts') or 0)}K"]
    earned = int(stats.get("earnedRuns") or 0)
    if earned:
        parts.append(f"失{earned}")
    return "・".join(parts)


def detail_from_boxscore(box: dict, player_id: int, role: str) -> str:
    """Pull one player's line out of a boxscore. '' if he is not in it."""
    for side in ("away", "home"):
        players = (((box.get("teams") or {}).get(side) or {}).get("players") or {})
        entry = players.get(f"ID{player_id}")
        if not entry:
            continue
        stats = entry.get("stats") or {}
        if role == "pitcher":
            return pitching_detail(stats.get("pitching") or {})
        return batting_detail(stats.get("batting") or {})
    return ""


def batting_order_detail(line: dict) -> str:
    """'第八棒' -- the one useful thing to say before a batter's first at-bat.

    It answers the question the stat line would have: where he sits in the
    order, and so roughly when he is up again.
    """
    order = (line.get("offense") or {}).get("battingOrder")
    try:
        slot = int(order)
    except (TypeError, ValueError):
        return ""
    return f"第{cn_number(slot)}棒" if 1 <= slot <= 9 else ""


# -- the poller ------------------------------------------------------------
class TaiwaneseWatcher:
    """Poll MLB and push when a Taiwanese player takes the plate or the mound.

    Alerts fire on a **transition**, which is what "on stage" means: the
    batter or the pitcher becomes someone he was not a moment ago. That
    gives exactly one buzz per plate appearance and one per relief
    appearance, with no watermark to maintain and nothing to dedupe -- a
    pitcher who works three innings is one alert, not fifty.

    The first look at a game is deliberately *not* silent, unlike the CPBL
    watcher's ``prime``. There, replaying earlier rallies would be telling
    you about something already over; here, a Taiwanese pitcher standing on
    the mound right now is the present tense, and it is the whole reason you
    installed this. The cost is that restarting the process re-announces
    whoever is on stage at that moment.
    """

    def __init__(
        self,
        client: MlbClient,
        notifier: Notifier,
        extra_players: list | None = None,
        poll_seconds: int = 20,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.notifier = notifier
        self.poll_seconds = max(poll_seconds, MIN_POLL_SECONDS)
        self.dry_run = dry_run
        self.stages: dict[int, Stage] = {}
        self.extra_ids: set[int] = set()
        self.extra_names: set[str] = set()
        for entry in extra_players or []:
            if entry is None or not str(entry).strip():
                continue
            try:
                self.extra_ids.add(int(entry))
            except (TypeError, ValueError):
                self.extra_names.add(str(entry).strip())
        self.roster: dict[int, str] = {}

    # -- membership --------------------------------------------------------
    def is_taiwanese(self, player: dict | None) -> bool:
        """Three sources, because none of them is complete on its own.

        The roster endpoint is authoritative but lags a call-up; the built-in
        :data:`TAIWANESE_NAMES` covers that gap for players it already knows;
        the config list is the escape hatch for anyone born abroad, whom
        ``birthCountry`` will never find.
        """
        if not player:
            return False
        pid = _person_id(player)
        name = str(player.get("fullName") or "")
        return bool(
            (pid is not None and (pid in self.roster or pid in TAIWANESE_IDS
                                  or pid in self.extra_ids))
            or name in TAIWANESE_NAMES
            or name in self.extra_names
        )

    # -- core --------------------------------------------------------------
    def process(self, games: list[dict]) -> int:
        """Run one schedule payload through the trigger. Returns alerts fired."""
        live = self.client.live(games)
        live_pks = {int(g.get("gamePk") or 0) for g in live}
        # A game that has ended keeps no stage: if it is resumed (suspended
        # games happen) it should read as a fresh arrival, not a continuation.
        for gone in set(self.stages) - live_pks:
            self.stages.pop(gone, None)

        fired = 0
        for game in live:
            try:
                fired += self._process_game(game)
            except Exception as exc:  # noqa: BLE001 - one bad game must not stop the rest
                log.warning("game %s failed: %s", game.get("gamePk"), exc)
        return fired

    def _process_game(self, game: dict) -> int:
        line = game.get("linescore") or {}
        state = state_from_mlb_game(game)
        if state is None:
            return 0

        game_pk = int(game.get("gamePk") or 0)
        batter = (line.get("offense") or {}).get("batter")
        pitcher = (line.get("defense") or {}).get("pitcher")
        previous = self.stages.get(game_pk)
        self.stages[game_pk] = Stage(_person_id(batter), _person_id(pitcher))

        roles = {"batter": batter, "pitcher": pitcher}
        here = {role for role, player in roles.items() if self.is_taiwanese(player)}
        # See :func:`cpbl_alert.stage.arrivals` -- a role that changed hands
        # since the last poll, which is one event per plate appearance and
        # one per relief appearance.
        arrived = arrivals(here, previous,
                           {role: _person_id(p) for role, p in roles.items()})
        if not arrived:
            return 0

        # Taiwanese pitcher against Taiwanese batter: one notification, not
        # two. Sending both would buzz twice for a single moment and make
        # each of them look like it was about someone else.
        if len(here) == 2:
            self._fire(state, game_pk, "duel", _person_id(roles["batter"]),
                       roles["batter"], line, with_detail=False)
            return 1

        for role in sorted(arrived):
            self._fire(state, game_pk, role, _person_id(roles[role]),
                       roles[role], line)
        return len(arrived)

    def _fire(self, state: GameState, game_pk: int, role: str, pid: int | None,
              player: dict | None, line: dict, with_detail: bool = True) -> None:
        # The boxscore is a second request, so it is only made when something
        # is actually being sent -- never on the poll that found nothing. A
        # duel needs no line of stats: whose would it be?
        detail = ""
        if with_detail and pid is not None:
            try:
                detail = detail_from_boxscore(self.client.boxscore(game_pk), pid, role)
            except (MlbError, requests.RequestException) as exc:
                log.debug("boxscore for game %s failed: %s", game_pk, exc)
            if not detail and role == "batter":
                detail = batting_order_detail(line)

        spot = Spotlight(role=role, player_id=pid,
                         name=display_name(player), detail=detail)
        text = format_stage_alert(state, spot)
        log.info("MLB ALERT game %s | %s %s | %s",
                 game_pk, role, spot.name, state.describe())
        if self.dry_run:
            print(text)
        else:
            self.notifier.send(text)

    # -- main loop ---------------------------------------------------------
    def _sleep_for(self, games: list[dict], live: list[dict]) -> int:
        """Poll pace when something is on, idle pace when nothing is close."""
        if live:
            return self.poll_seconds
        now = dt.datetime.now(dt.timezone.utc)
        soon = now + dt.timedelta(minutes=WARMUP_MINUTES)
        for game in games:
            starts_at = parse_game_time(game.get("gameDate"))
            if starts_at is not None and now <= starts_at <= soon:
                return self.poll_seconds
        return IDLE_SLEEP

    def run(self, once: bool = False) -> None:
        while True:
            start, end = window()
            try:
                self.roster = self.client.taiwanese_players(end[:4], end)
            except (MlbError, requests.RequestException) as exc:
                log.warning("roster lookup failed: %s", exc)
            try:
                games = self.client.schedule(start, end)
            except (MlbError, requests.RequestException) as exc:
                log.warning("schedule lookup failed: %s", exc)
                games = []

            live = self.client.live(games)
            if games:
                self.process(games)
            if once:
                return
            sleep = self._sleep_for(games, live)
            log.debug("%d live game(s); sleeping %ss", len(live), sleep)
            time.sleep(sleep)


def parse_game_time(value: object) -> dt.datetime | None:
    """MLB's ``gameDate`` ('2026-08-28T22:40:00Z') as an aware datetime."""
    try:
        return dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def window(now: dt.datetime | None = None) -> tuple[str, str]:
    """The two US business dates that can hold a game happening right now.

    A game listed under Aug 28 can start at 00:15 UTC on the 29th, so "what
    is on now" always straddles two of MLB's dates -- and from Taiwan, where
    an evening game in the States is tomorrow morning, it is the *earlier*
    of the two that is usually the interesting one.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    return ((now - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            now.strftime("%Y-%m-%d"))
