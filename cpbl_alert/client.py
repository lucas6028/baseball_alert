"""HTTP client for the CPBL live endpoints.

There is no public CPBL API; these are the endpoints the official site's own
front-end calls. Everything awkward about them is handled here so callers
never see it:

  1. **Two different anti-forgery tokens.** ``/box/getlive`` wants the ASP.NET
     ``__RequestVerificationToken`` as a *form field*, scraped from the
     ``/box`` page's hidden input. ``/schedule/getgamedatas`` wants a
     different token as a ``RequestVerificationToken`` *header*, inlined in
     the ``/schedule`` page's JavaScript. Both expire; both are re-scraped and
     retried on rejection.

  2. **HiNetCDN cookie challenge.** The first request to a path returns 307/308
     with ``Location`` pointing at that same path plus a ``__chtcdn`` cookie.
     Replaying with the cookie succeeds, so every request retries.

  3. **Status lives in the wrong place.** The schedule payload's ``GameStatus``
     is null; it only tells you a game is *scheduled* (plus its start/end
     times). Whether a game is actually in progress comes from
     ``/box/getlive`` -> ``CurtGameDetailJson.GameStatus``.

Be a polite client: this is someone else's website, not a paid API.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time

import requests

BASE = "https://www.cpbl.com.tw"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

MIN_POLL_SECONDS = 10

# GameStatus values, from the site's own scoreboard logic.
STATUS_PREGAME = 1
STATUS_LIVE = 2
STATUS_FINAL = 3
STATUS_LINEUP = 4
STATUS_DELAYED = 8
LIVE_STATUSES = frozenset({STATUS_LIVE, STATUS_DELAYED})
DONE_STATUSES = frozenset({STATUS_FINAL})

_FORM_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_HEADER_TOKEN_RE = re.compile(r"RequestVerificationToken:\s*'([^']+)'")
_GAMESNO_RE = re.compile(r'id="GameSno"[^>]*value="([^"]*)"')

log = logging.getLogger(__name__)


class CpblError(RuntimeError):
    pass


class CpblClient:
    """Session-aware client. Reuse one instance for the life of the poller."""

    def __init__(self, timeout: int = 20, min_interval: float = 1.0) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        self._form_token: str | None = None
        self._header_token: str | None = None
        self._default_game_sno: str | None = None
        self._schedule_cache: tuple[str, list[dict]] | None = None
        # Games known to have finished, per game-day. A finished game never
        # goes live again, so re-fetching its 400KB payload every poll for the
        # rest of the night is pure waste on someone else's server.
        self._finished: dict[str, set[int]] = {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-TW,zh;q=0.9",
        })

    # -- internals ---------------------------------------------------------
    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_request = time.monotonic()

    def _get_page(self, path: str) -> str:
        """GET a page, absorbing the CDN cookie challenge."""
        for _ in range(3):
            self._throttle()
            resp = self.session.get(f"{BASE}{path}", timeout=self.timeout,
                                    allow_redirects=False)
            if resp.status_code in (307, 308):
                continue
            resp.raise_for_status()
            return resp.text
        raise CpblError(f"could not load {path} (CDN challenge did not clear)")

    def _refresh_form_token(self) -> str:
        html = self._get_page("/box")
        match = _FORM_TOKEN_RE.search(html)
        if not match:
            raise CpblError("no __RequestVerificationToken on /box")
        self._form_token = match.group(1)
        sno = _GAMESNO_RE.search(html)
        self._default_game_sno = sno.group(1) if sno else None
        return self._form_token

    def _refresh_header_token(self) -> str:
        html = self._get_page("/schedule")
        # Several tokens are inlined; the one guarding getgamedatas is the
        # last one declared before that call.
        idx = html.find("/schedule/getgamedatas")
        window = html[:idx] if idx > 0 else html
        matches = _HEADER_TOKEN_RE.findall(window)
        if not matches:
            raise CpblError("no RequestVerificationToken on /schedule")
        self._header_token = matches[-1]
        return self._header_token

    def _post(self, path: str, data: dict, referer: str,
              *, header_token: bool = False) -> dict:
        """POST with token refresh + CDN-challenge retry baked in."""
        for _ in range(4):
            headers = {"X-Requested-With": "XMLHttpRequest",
                       "Referer": f"{BASE}{referer}",
                       "Origin": BASE}
            payload = dict(data)
            if header_token:
                headers["RequestVerificationToken"] = (
                    self._header_token or self._refresh_header_token())
            else:
                payload["__RequestVerificationToken"] = (
                    self._form_token or self._refresh_form_token())

            self._throttle()
            resp = self.session.post(f"{BASE}{path}", data=payload, headers=headers,
                                     timeout=self.timeout, allow_redirects=False)

            if resp.status_code in (307, 308):
                log.debug("CDN challenge on %s, retrying", path)
                continue
            if resp.status_code in (400, 403, 419, 500):
                log.debug("token rejected on %s (%s), refreshing", path, resp.status_code)
                if header_token:
                    self._header_token = None
                else:
                    self._form_token = None
                continue
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as exc:
                raise CpblError(
                    f"{path} returned non-JSON ({len(resp.content)} bytes)") from exc

        raise CpblError(f"{path} did not succeed after retries")

    # -- schedule ----------------------------------------------------------
    def season_games(self, year: str, kind_code: str = "A",
                     refresh: bool = False) -> list[dict]:
        """Every game in a season. Cached -- this is a ~440KB payload.

        The endpoint takes Jan 1 of the year and returns the whole season; the
        official site filters client-side, and so do we.
        """
        cache_key = f"{year}-{kind_code}"
        if not refresh and self._schedule_cache and self._schedule_cache[0] == cache_key:
            return self._schedule_cache[1]

        result = self._post(
            "/schedule/getgamedatas",
            {"calendar": f"{year}/01/01", "location": "", "kindCode": kind_code},
            referer="/schedule",
            header_token=True,
        )
        if not result.get("Success"):
            raise CpblError("getgamedatas returned Success=false")
        raw = result.get("GameDatas") or "[]"
        games = json.loads(raw) if isinstance(raw, str) else raw
        games = games or []
        self._schedule_cache = (cache_key, games)
        return games

    def games_on(self, gameday: str, year: str | None = None,
                 kind_code: str = "A") -> list[dict]:
        """Games scheduled on ``gameday`` (YYYY-MM-DD)."""
        year = year or gameday[:4]
        return [g for g in self.season_games(year, kind_code)
                if str(g.get("GameDate", "")).startswith(gameday)]

    # -- live --------------------------------------------------------------
    def game_live(self, game_sno: int | str, year: str, kind_code: str = "A") -> dict:
        """Full live payload for one game, with the embedded JSON decoded."""
        result = self._post(
            "/box/getlive",
            {"GameSno": str(game_sno), "Year": str(year), "KindCode": kind_code,
             "PrevOrNext": "", "PresentStatus": ""},
            referer="/box",
        )
        if not result.get("Success"):
            raise CpblError(f"getlive returned Success=false for game {game_sno}")

        def _load(key):
            raw = result.get(key)
            if not raw:
                return None
            return json.loads(raw) if isinstance(raw, str) else raw

        meta = _load("CurtGameDetailJson") or {}
        return {
            "meta": meta,
            "rows": _load("LiveLogJson") or [],
            "scoreboard": _load("ScoreboardJson") or [],
            "status": int(meta.get("GameStatus") or 0),
        }

    def live_games(self, gameday: str, year: str | None = None,
                   kind_code: str = "A", grace_minutes: int = 30) -> list[dict]:
        """Games actually in progress right now.

        The schedule alone cannot tell us this, so we check ``getlive`` for
        each of today's games -- but only those whose scheduled start has
        passed, so we don't waste requests on a 6pm game at noon.
        """
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)
        finished = self._finished.setdefault(gameday, set())
        live: list[dict] = []
        for game in self.games_on(gameday, year, kind_code):
            start = str(game.get("GameDateTimeS") or "")
            if start:
                try:
                    starts_at = dt.datetime.fromisoformat(start)
                    if now < starts_at - dt.timedelta(minutes=grace_minutes):
                        continue        # hasn't started yet
                except ValueError:
                    pass
            sno = game.get("GameSno")
            if sno is None or int(sno) in finished:
                continue
            try:
                payload = self.game_live(sno, str(game.get("Year") or gameday[:4]),
                                         str(game.get("KindCode") or kind_code))
            except (CpblError, requests.RequestException) as exc:
                log.debug("status check failed for game %s: %s", sno, exc)
                continue
            if payload["status"] in DONE_STATUSES:
                finished.add(int(sno))
                log.debug("game %s finished; will not poll it again today", sno)
                continue
            if payload["status"] in LIVE_STATUSES:
                live.append({**game, "GameStatus": payload["status"], "_payload": payload})
        return live

    @property
    def default_game_sno(self) -> str | None:
        """Whatever game /box defaults to -- handy as a fallback."""
        if self._default_game_sno is None:
            self._refresh_form_token()
        return self._default_game_sno
