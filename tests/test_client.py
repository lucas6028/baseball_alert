"""Client retry logic -- CDN challenge and token refresh, without network."""

import json

import pytest

from cpbl_alert.client import CpblClient, CpblError


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.content = text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


BOX_HTML = ('<form><input name="__RequestVerificationToken" type="hidden" value="TOK123" />'
            '<input id="GameSno" name="GameSno" type="hidden" value="290" /></form>')

SCHEDULE_HTML = (
    "<script>RequestVerificationToken: 'EARLY-TOKEN'\n"
    "RequestVerificationToken: 'SCHED-TOKEN'\n"
    "url: '/schedule/getgamedatas'</script>"
)


def _pages(path_map):
    def fake_get(url, **kw):
        for path, html in path_map.items():
            if url.endswith(path):
                return FakeResp(200, text=html)
        raise AssertionError(f"unexpected GET {url}")
    return fake_get


@pytest.fixture
def client(monkeypatch):
    c = CpblClient(min_interval=0)
    monkeypatch.setattr(c.session, "get",
                        _pages({"/box": BOX_HTML, "/schedule": SCHEDULE_HTML}))
    return c


# -- token handling --------------------------------------------------------

def test_form_token_is_scraped_from_box_page(client):
    assert client._refresh_form_token() == "TOK123"
    assert client.default_game_sno == "290"


def test_header_token_picks_the_one_guarding_getgamedatas(client):
    """Several tokens are inlined; we need the last one before the call."""
    assert client._refresh_header_token() == "SCHED-TOKEN"


def test_getlive_sends_token_as_form_field(client, monkeypatch):
    seen = {}

    def fake_post(url, data=None, headers=None, **kw):
        seen["data"] = data
        seen["headers"] = headers
        return FakeResp(200, payload={"Success": True, "LiveLogJson": "[]",
                                      "CurtGameDetailJson": "{}"})

    monkeypatch.setattr(client.session, "post", fake_post)
    client.game_live(290, "2026")
    assert seen["data"]["__RequestVerificationToken"] == "TOK123"
    assert "RequestVerificationToken" not in seen["headers"]


def test_schedule_sends_token_as_header(client, monkeypatch):
    seen = {}

    def fake_post(url, data=None, headers=None, **kw):
        seen["data"] = data
        seen["headers"] = headers
        return FakeResp(200, payload={"Success": True, "GameDatas": "[]"})

    monkeypatch.setattr(client.session, "post", fake_post)
    client.season_games("2026")
    assert seen["headers"]["RequestVerificationToken"] == "SCHED-TOKEN"
    assert "__RequestVerificationToken" not in seen["data"]
    # The endpoint wants Jan 1 of the season, not today.
    assert seen["data"]["calendar"] == "2026/01/01"


# -- retry behaviour -------------------------------------------------------

def test_cdn_308_challenge_is_retried(client, monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return FakeResp(308)
        return FakeResp(200, payload={"Success": True, "LiveLogJson": "[]",
                                      "CurtGameDetailJson": "{}"})

    monkeypatch.setattr(client.session, "post", fake_post)
    out = client.game_live(290, "2026")
    assert len(calls) == 2
    assert out["rows"] == []


@pytest.mark.parametrize("status", [400, 403, 500])
def test_rejected_token_triggers_refresh(client, monkeypatch, status):
    """A 500 from getgamedatas is what a stale token actually looks like."""
    posts = []

    def fake_post(url, data=None, headers=None, **kw):
        posts.append(1)
        if len(posts) == 1:
            return FakeResp(status)
        return FakeResp(200, payload={"Success": True, "LiveLogJson": "[]",
                                      "CurtGameDetailJson": "{}"})

    monkeypatch.setattr(client.session, "post", fake_post)
    client.game_live(290, "2026")
    assert len(posts) == 2


def test_repeated_challenge_eventually_raises(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResp(308))
    with pytest.raises(CpblError):
        client.game_live(290, "2026")


def test_success_false_raises(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResp(
        200, payload={"Success": False}))
    with pytest.raises(CpblError):
        client.game_live(290, "2026")


# -- schedule parsing ------------------------------------------------------

SEASON = [
    {"GameSno": 288, "GameDate": "2026-08-26T00:00:00", "Year": "2026",
     "GameDateTimeS": "2026-08-26T18:35:00"},
    {"GameSno": 290, "GameDate": "2026-08-26T00:00:00", "Year": "2026",
     "GameDateTimeS": "2026-08-26T18:35:00"},
    {"GameSno": 300, "GameDate": "2026-09-01T00:00:00", "Year": "2026",
     "GameDateTimeS": "2026-09-01T18:35:00"},
]


def test_games_on_filters_by_date(client, monkeypatch):
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResp(
        200, payload={"Success": True, "GameDatas": json.dumps(SEASON)}))
    assert [g["GameSno"] for g in client.games_on("2026-08-26")] == [288, 290]


def test_season_schedule_is_cached(client, monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResp(200, payload={"Success": True, "GameDatas": json.dumps(SEASON)})

    monkeypatch.setattr(client.session, "post", fake_post)
    client.games_on("2026-08-26")
    client.games_on("2026-08-27")
    assert len(calls) == 1, "the 440KB season payload must not be refetched"


def test_embedded_json_strings_are_decoded(client, monkeypatch):
    rows = [{"InningSeq": 1, "Pkno": "a"}]
    monkeypatch.setattr(client.session, "post", lambda *a, **k: FakeResp(
        200, payload={"Success": True, "LiveLogJson": json.dumps(rows),
                      "CurtGameDetailJson": json.dumps({"GameSno": 290, "GameStatus": 2})}))
    out = client.game_live(290, "2026")
    assert out["rows"] == rows
    assert out["meta"]["GameSno"] == 290
    assert out["status"] == 2


def test_live_games_uses_getlive_status_not_schedule(client, monkeypatch):
    """Schedule GameStatus is null in reality; status must come from getlive."""
    statuses = {288: 3, 290: 2}   # 288 final, 290 live

    def fake_post(url, data=None, **kw):
        if "getgamedatas" in url:
            return FakeResp(200, payload={"Success": True,
                                          "GameDatas": json.dumps(SEASON)})
        sno = int(data["GameSno"])
        return FakeResp(200, payload={
            "Success": True, "LiveLogJson": "[]",
            "CurtGameDetailJson": json.dumps({"GameStatus": statuses[sno]})})

    monkeypatch.setattr(client.session, "post", fake_post)
    live = client.live_games("2026-08-26")
    assert [g["GameSno"] for g in live] == [290]


def test_live_games_skips_games_that_have_not_started(client, monkeypatch):
    """A 6pm game must not be polled at noon."""
    future = [{"GameSno": 999, "GameDate": "2099-01-01T00:00:00", "Year": "2099",
               "GameDateTimeS": "2099-01-01T18:35:00"}]
    posts = []

    def fake_post(url, data=None, **kw):
        posts.append(url)
        if "getgamedatas" in url:
            return FakeResp(200, payload={"Success": True,
                                          "GameDatas": json.dumps(future)})
        raise AssertionError("should not have polled an unstarted game")

    monkeypatch.setattr(client.session, "post", fake_post)
    assert client.live_games("2099-01-01") == []


def test_finished_games_are_not_polled_again(client, monkeypatch):
    """A game that ended at 21:40 must not be refetched every 15s till midnight."""
    getlive_calls = []

    def fake_post(url, data=None, **kw):
        if "getgamedatas" in url:
            return FakeResp(200, payload={"Success": True,
                                          "GameDatas": json.dumps(SEASON)})
        getlive_calls.append(int(data["GameSno"]))
        return FakeResp(200, payload={
            "Success": True, "LiveLogJson": "[]",
            "CurtGameDetailJson": json.dumps({"GameStatus": 3})})   # both final

    monkeypatch.setattr(client.session, "post", fake_post)
    assert client.live_games("2026-08-26") == []
    assert sorted(getlive_calls) == [288, 290]

    getlive_calls.clear()
    assert client.live_games("2026-08-26") == []
    assert getlive_calls == [], "finished games must not be polled again"


def test_a_live_game_keeps_being_polled(client, monkeypatch):
    """Only terminal status is cached -- a live game must still be re-checked."""
    calls = []

    def fake_post(url, data=None, **kw):
        if "getgamedatas" in url:
            return FakeResp(200, payload={"Success": True,
                                          "GameDatas": json.dumps(SEASON)})
        sno = int(data["GameSno"])
        calls.append(sno)
        status = 2 if sno == 290 else 3
        return FakeResp(200, payload={
            "Success": True, "LiveLogJson": "[]",
            "CurtGameDetailJson": json.dumps({"GameStatus": status})})

    monkeypatch.setattr(client.session, "post", fake_post)
    assert [g["GameSno"] for g in client.live_games("2026-08-26")] == [290]
    calls.clear()
    assert [g["GameSno"] for g in client.live_games("2026-08-26")] == [290]
    assert calls == [290], "the finished game is skipped, the live one is not"
