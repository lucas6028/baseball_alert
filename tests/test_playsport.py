"""The playsport live feed: the enum, the three defects, the ordering guard.

Everything here runs off tests/fixtures/playsport_live.json, which is real
traffic recorded on 2026-09-04 (games 307/308/309). No network.
"""

import io
import logging
import json
import os

import pytest

from cpbl_alert import playsport
from cpbl_alert.dedupe import GameTracker
from cpbl_alert.leverage import assess
from cpbl_alert.notifier import format_alert
from cpbl_alert.playsport import (
    LiveGuard,
    PlaysportClient,
    PlaysportSource,
    decode_bases,
    parse_record,
    state_from_record,
)
from cpbl_alert.watcher import Watcher

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "playsport_live.json")

DRAGONS = "CPBL_20260904_DRAGONS@BRO_1835"
UNI = "CPBL_20260904_13342@UNI_1835"


@pytest.fixture(scope="session")
def feed():
    with io.open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["records"]


def labelled(feed, label):
    return [r for r in feed if r["label"] == label]


def one(feed, label):
    return labelled(feed, label)[0]


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


# -- the rs enum -----------------------------------------------------------
def test_rs_is_an_enum_index_not_a_bitmask():
    """4 is 一二壘 and 3 is 三壘 -- a bitmask reading swaps exactly these two.

    Verified 91/94 against CPBL's own base occupancy; the bitmask reading
    scores 62/94. Getting it backwards invents runners in scoring position.
    """
    assert decode_bases(4) == (True, True, False), "4 must be 一二壘, not 三壘"
    assert decode_bases(3) == (False, False, True), "3 must be 三壘, not 一二壘"


def test_rs_decodes_the_whole_enum():
    expected = {
        0: "---", 1: "1--", 2: "-2-", 3: "--3",
        4: "12-", 5: "1-3", 6: "-23", 7: "123",
    }
    for code, want in expected.items():
        first, second, third = decode_bases(code)
        got = "".join(d if occ else "-"
                      for d, occ in (("1", first), ("2", second), ("3", third)))
        assert got == want, f"rs={code} should be {want}"


def test_string_and_int_records_decode_identically(feed):
    """Two record shapes are interleaved and both must parse.

    Type A types ``rs`` as int with ``ss`` null; type B types it as str with
    ``ss`` a string. Neither is systematically fresher, so both are consumed.
    """
    a = parse_record(one(feed, "live_typeA"))
    b = parse_record(one(feed, "live_typeB"))
    assert a.first is True and b.first is True
    assert a.inning == b.inning == 7
    assert a.is_top is b.is_top is True


# -- D1: the corrupt Final -------------------------------------------------
def test_a_final_record_is_dropped(feed):
    """``ss == "Final"`` mirrors the home score into the away score.

    The fixture's 11-11 is game 308, which truly ended 0-11. Believed, it is
    a tie game in the 9th -- the single loudest alert this thing can send.
    """
    guard = LiveGuard()
    corrupt = parse_record(one(feed, "corrupt_final"))
    assert (corrupt.visiting_score, corrupt.home_score) == (11, 11)
    assert guard.accept(corrupt) is False


def test_a_final_record_never_reaches_the_phone(feed):
    """End to end: the corrupt tie must not produce an alert."""
    notifier = RecordingNotifier()
    watcher = Watcher(_StubClient(), notifier)
    guard = LiveGuard()
    for raw in labelled(feed, "corrupt_final"):
        record = parse_record(raw)
        if guard.accept(record):
            watcher.process_state(state_from_record(record), 308)
    assert notifier.sent == [], "a corrupt Final must never alert"


def test_a_final_record_does_not_poison_the_high_water_mark(feed):
    """The reason D1 is checked *before* the mark is updated.

    11-11 is 22 total runs. If that reached the high-water mark the real 6-5
    game would sit forever below it and the feed would go silent for the rest
    of the night -- while still, misleadingly, sending no bad alert.
    """
    guard = LiveGuard()
    corrupt = parse_record(one(feed, "corrupt_final"))
    corrupt.oid = DRAGONS                       # same game as the live records
    guard.accept(corrupt)
    live = parse_record(one(feed, "live_typeA"))
    assert guard.accept(live) is True, "a real record must survive a corrupt Final"


# -- D2: three outs --------------------------------------------------------
def test_a_three_out_record_is_dropped(feed):
    """CPBL never publishes 3 outs as a pre-pitch state.

    The LI table clamps outs to 2, so believing this record would just
    duplicate the 2-out alert one poll later.
    """
    guard = LiveGuard()
    for raw in labelled(feed, "three_outs"):
        record = parse_record(raw)
        assert record.outs == 3
        assert guard.accept(record) is False


# -- D3: the game is over --------------------------------------------------
def test_a_game_over_record_marks_the_game_finished(feed):
    """``結束``/``比賽結束`` is the trustworthy end marker; stop polling."""
    guard = LiveGuard()
    over = parse_record(one(feed, "game_over"))
    assert guard.accept(over) is False
    assert guard.is_finished(over.oid) is True
    assert guard.is_finished(DRAGONS) is False, "only that game stops"


def test_a_finished_game_ignores_everything_after(feed):
    guard = LiveGuard()
    guard.accept(parse_record(one(feed, "game_over")))
    later = parse_record(one(feed, "corrupt_final"))
    assert later.oid == UNI
    assert guard.accept(later) is False


# -- the monotonic guard ---------------------------------------------------
def test_the_guard_drops_a_record_that_goes_backwards(feed):
    """The raw stream regressed on 22 of 2337 samples; this removes them."""
    guard = LiveGuard()
    forward = parse_record(one(feed, "live_typeB"))     # 7上, 1 out, 6-5
    assert guard.accept(forward) is True
    backward = parse_record(one(feed, "live_typeA"))    # 7上, 1 out, 4-5
    assert backward.visiting_score == 4
    assert guard.accept(backward) is False


def test_the_guard_ignores_the_ball_strike_count(feed):
    """The count must not be in the ordering tuple.

    Balls/strikes reset to 0-0 on every new batter, so a tuple containing
    them would rank the *fresher* record lower and throw it away. LI never
    reads the count, so leaving it out costs nothing.
    """
    guard = LiveGuard()
    counted = parse_record(one(feed, "live_typeB"))     # s=0 in the fixture
    counted_later = parse_record(labelled(feed, "live_typeB")[1])  # s=2
    assert guard.accept(counted_later) is True
    # Same situation, count back at 0-0 because a new batter stepped in.
    assert guard.accept(counted) is True, "a count reset must not look stale"


def test_the_high_water_mark_is_per_game(feed):
    guard = LiveGuard()
    ahead = parse_record(one(feed, "live_typeB"))
    assert guard.accept(ahead) is True
    other = parse_record(one(feed, "live_typeA"))
    other.oid = "CPBL_20260904_RAKUTEN@FUBON_1835"
    assert guard.accept(other) is True, "another game has its own mark"


# -- GameState construction ------------------------------------------------
def test_state_carries_the_situation_and_blanks_the_rest(feed):
    record = parse_record(one(feed, "live_typeA"))
    state = state_from_record(record, {"GameSno": 309, "Year": "2026",
                                       "VisitingTeamName": "味全龍",
                                       "HomeTeamName": "中信兄弟"})
    assert state.inning == 7 and state.is_top and state.outs == 1
    assert state.base_code() == "1--"
    assert (state.visiting_score, state.home_score) == (4, 5)
    assert state.game_sno == 309
    assert (state.balls, state.strikes, state.batter, state.pitcher) == (0, 0, "", "")


def test_pitch_id_is_exactly_the_dedupe_key(feed):
    """The identity is the situation, stated outright as ``event_no``.

    ``GameState`` returns ``event_no`` verbatim from ``pitch_id``, so this
    takes the identity out of the hands of the composite fallback -- which
    would fold in balls/strikes and the batter's name. It carries the same six
    things ``GameTracker._key`` does, so ``dedupe.py`` needs no change.
    """
    record = parse_record(one(feed, "live_typeA"))
    state = state_from_record(record)
    key = GameTracker._key(state)
    assert state.pitch_id == (
        f"{key[0]}|{key[1]}|{key[2]}|{key[3]}|{key[4]}-{key[5]}")


def test_pitch_id_survives_a_count_change(feed):
    """The count resets to 0-0 on every batter; it cannot be an identity."""
    a, b = (parse_record(r) for r in labelled(feed, "live_typeB")[:2])
    # The fixture's two samples are the same situation at 0-0 and at 0-2.
    b.first, b.second, b.third = a.first, a.second, a.third
    assert state_from_record(a).pitch_id == state_from_record(b).pitch_id


def test_pitch_id_survives_a_batter_change(feed):
    """``gs.br``/``gs.pr`` populate on ~6% of records and blink in and out.

    In the identity, that flicker alone would re-announce a rally that has not
    moved; the names are for the reader, not for the watermark.
    """
    named = parse_record(one(feed, "live_typeA"))
    anonymous = parse_record(one(feed, "live_typeA"))
    named.batter, named.pitcher = "張育成", "賴威誠"
    assert anonymous.batter == ""
    assert state_from_record(named).pitch_id == state_from_record(anonymous).pitch_id


@pytest.mark.parametrize("field, value", [
    ("outs", 2), ("is_top", False), ("second", True), ("home_score", 9),
])
def test_pitch_id_changes_when_the_situation_does(feed, field, value):
    """...and it must still move when something that matters moves."""
    before = parse_record(one(feed, "live_typeA"))
    after = parse_record(one(feed, "live_typeA"))
    setattr(after, field, value)
    assert state_from_record(after).pitch_id != state_from_record(before).pitch_id


def test_the_alert_has_no_dangling_label_without_names(feed):
    """The feed usually gives no matchup; a bare '打者 ' must not be printed."""
    record = parse_record(one(feed, "live_typeA"))
    record.first, record.second, record.third = True, True, True
    record.visiting_score, record.home_score = 5, 5
    state = state_from_record(record, {"VisitingTeamName": "味全龍",
                                       "HomeTeamName": "中信兄弟"})
    assert (state.batter, state.pitcher) == ("", "")
    lines = format_alert(state, assess(state)).splitlines()
    assert len(lines) == 4, "the diamond keeps its two rows either way"
    assert "打者" not in lines[1] and "投手" not in lines[2]
    assert lines[1] == lines[1].rstrip(), "no dangling whitespace either"
    assert "◆" in lines[1] and "◆" in lines[2]


def test_the_alert_shows_the_matchup_when_the_feed_has_one(feed):
    record = parse_record(labelled(feed, "three_outs")[1])
    assert (record.batter, record.pitcher) == ("張育成", "賴威誠")
    record.outs = 1
    state = state_from_record(record)
    lines = format_alert(state, assess(state)).splitlines()
    assert lines[1].endswith("打者 張育成")
    assert lines[2].endswith("投手 賴威誠")


def test_the_same_situation_only_alerts_once(feed):
    notifier = RecordingNotifier()
    watcher = Watcher(_StubClient(), notifier)
    record = parse_record(one(feed, "live_typeA"))
    record.first, record.second, record.third = True, True, True
    record.visiting_score, record.home_score = 5, 5
    state = state_from_record(record, {"GameSno": 309})
    assert assess(state).should_alert, "滿壘 5-5 in the 7th should be an alert"
    assert watcher.process_state(state, 309) == 1
    assert watcher.process_state(state, 309) == 0, "same situation, one alert"


# -- OID matching ----------------------------------------------------------
def test_oid_teams_resolves_both_clubs():
    assert playsport.oid_teams(DRAGONS) == ("味全龍", "中信兄弟")


def test_an_unresolvable_oid_matches_nothing():
    """13342 is a bare numeric club id; half a match is not a match."""
    assert playsport.oid_teams(UNI) is None
    assert playsport.oid_teams("garbage") is None


# -- watcher wiring --------------------------------------------------------
class _StubClient:
    def game_live(self, sno, year, kind_code="A"):
        return {"meta": {}, "rows": [], "scoreboard": [], "status": 2}

    def live_games(self, gameday, **kw):
        return []


class _StubSource:
    """A PlaysportSource stand-in: no network, scripted answers."""

    def __init__(self, state=None, available=True):
        self.state = state
        self._available = available
        self.released = 0

    def available(self, game, gameday):
        return self._available

    def state_for(self, game, gameday):
        return self.state

    def release(self, game, gameday):
        self.released += 1


def _game(sno=309):
    return {"GameSno": sno, "Year": "2026", "KindCode": "A",
            "VisitingTeamName": "味全龍", "HomeTeamName": "中信兄弟"}


def test_default_watcher_never_builds_a_playsport_source():
    """Existing behaviour is unchanged unless the config asks for it."""
    watcher = Watcher(_StubClient(), RecordingNotifier())
    assert watcher.cpbl_source == "cpbl"
    assert watcher.playsport is None
    assert watcher.source_for(_game(), 309, "2026-09-04") == "cpbl"


def test_playsport_is_chosen_when_available(feed):
    source = _StubSource()
    watcher = Watcher(_StubClient(), RecordingNotifier(),
                      cpbl_source="playsport", playsport=source)
    assert watcher.source_for(_game(), 309, "2026-09-04") == "playsport"


def test_a_game_without_an_oid_falls_back_to_the_official_feed():
    source = _StubSource(available=False)
    watcher = Watcher(_StubClient(), RecordingNotifier(),
                      cpbl_source="playsport", playsport=source)
    assert watcher.source_for(_game(), 309, "2026-09-04") == "cpbl"


def test_switching_source_resets_and_reprimes_the_tracker(feed):
    """The two feeds mint different pitch ids, so a switch must not replay.

    The tracker is wiped and re-primed in the new scheme; the situation that
    was live at the moment of the switch is marked seen, not re-announced.
    """
    record = parse_record(one(feed, "live_typeA"))
    record.first, record.second, record.third = True, True, True
    record.visiting_score, record.home_score = 5, 5
    state = state_from_record(record, {"GameSno": 309})
    source = _StubSource(state=state, available=False)
    notifier = RecordingNotifier()
    watcher = Watcher(_StubClient(), notifier,
                      cpbl_source="playsport", playsport=source)

    assert watcher.source_for(_game(), 309, "2026-09-04") == "cpbl"
    watcher.trackers[309] = GameTracker()
    watcher.trackers[309].seen_pitches.add("stale-cpbl-id")

    source._available = True
    assert watcher.source_for(_game(), 309, "2026-09-04") == "playsport"
    tracker = watcher.trackers[309]
    assert "stale-cpbl-id" not in tracker.seen_pitches, "the old memory is dropped"
    assert state.pitch_id in tracker.seen_pitches, "the live moment is primed, not sent"
    assert source.released == 1
    assert notifier.sent == []
    assert watcher.process_state(state, 309) == 0


def test_the_team_filter_applies_to_the_playsport_path(feed):
    record = parse_record(one(feed, "live_typeA"))
    record.first, record.second, record.third = True, True, True
    record.visiting_score, record.home_score = 5, 5
    state = state_from_record(record, _game())
    notifier = RecordingNotifier()
    watcher = Watcher(_StubClient(), notifier, teams=["樂天桃猿"],
                      cpbl_source="playsport", playsport=_StubSource(state))
    assert watcher.process_state(state, 309, _game()) == 0
    assert notifier.sent == []


class _StubFeedClient:
    """A PlaysportClient stand-in with a canned OID list and canned entries."""

    def __init__(self, entries_by_oid, oid_ttl=180.0):
        self.entries = entries_by_oid
        self.oid_ttl = oid_ttl
        self.live_calls = []
        self.oid_calls = 0

    def oids(self, gamedate, refresh=False):
        self.oid_calls += 1
        return list(self.entries)

    def live(self, gamedate, oids):
        self.live_calls.append((gamedate, list(oids)))
        return [e for oid in oids for e in self.entries.get(oid, [])]


def _entry(oid, aname, hname, gs=None, runs=("0", "0")):
    """One ls_json game entry. The names are what matching actually uses."""
    return {"official_id": oid, "aname": aname, "hname": hname,
            "r": list(runs),
            "gs": gs or {"i": "7", "s": "0", "b": "0", "o": "1", "ti": "Y",
                         "rs": 0, "ss": None, "br": "", "pr": ""}}


def test_a_numeric_club_code_is_still_matched_by_name(feed):
    """The defect this replaced a code table to fix.

    CPBL_20260904_13342@UNI_1835 is 台鋼雄鷹 at 統一7-ELEVEn獅, and 13342 is a
    bare club id that no table here can resolve -- one of the three real games
    recorded that night. The live response's own aname/hname identify it.
    """
    source = PlaysportSource(client=_StubFeedClient(
        {UNI: [_entry(UNI, "雄鷹", "獅")]}))
    game = {"VisitingTeamName": "台鋼雄鷹", "HomeTeamName": "統一7-ELEVEn獅"}
    assert playsport.oid_teams(UNI) is None, "the code table cannot do this"
    assert source.oid_for(game, "2026-09-04") == UNI
    assert source.available(game, "2026-09-04") is True


def test_oid_for_matches_a_game_by_its_two_clubs():
    source = PlaysportSource(client=_StubFeedClient(
        {UNI: [_entry(UNI, "雄鷹", "獅")],
         DRAGONS: [_entry(DRAGONS, "龍", "兄弟")]}))
    assert source.oid_for(_game(), "2026-09-04") == DRAGONS


def test_a_game_playsport_does_not_carry_gets_no_oid(caplog):
    """And says so out loud -- silence here is what hid the last bug."""
    source = PlaysportSource(client=_StubFeedClient(
        {UNI: [_entry(UNI, "雄鷹", "獅")]}))
    with caplog.at_level("INFO"):
        assert source.oid_for(_game(), "2026-09-04") is None
    assert any("味全龍" in r.getMessage() for r in caplog.records), \
        "an unmatched game must be logged, not swallowed"
    assert source.available(_game(), "2026-09-04") is False


def test_the_oid_list_is_rescraped_once_it_goes_stale():
    """A fetch-once cache freezes the list at the first poll of the day.

    The livescore page lists only unfinished games, so a 21:00 game is simply
    not on it at 18:40 -- and would spend the whole run on the slower feed.
    """
    client = PlaysportClient(oid_ttl=180.0)
    now = [1000.0]
    client._clock = lambda: now[0]
    pages = ['<i data-oid="CPBL_20260904_DRAGONS@BRO_1835">',
             '<i data-oid="CPBL_20260904_DRAGONS@BRO_1835">'
             '<i data-oid="CPBL_20260904_13342@UNI_1835">']
    calls = []

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    class _Session:
        headers = {}

        def get(self, url, **kw):
            calls.append(url)
            return _Resp(pages[min(len(calls) - 1, 1)])

    client._session = _Session()
    assert client.oids("20260904") == [DRAGONS]
    now[0] += 60.0
    assert client.oids("20260904") == [DRAGONS] and len(calls) == 1, "still fresh"
    now[0] += 200.0
    assert client.oids("20260904") == [DRAGONS, UNI], "the later game appears"
    assert len(calls) == 2


class _MetaClient(_StubClient):
    """live_games() as the real one shapes it: schedule row + _payload."""

    def __init__(self, meta):
        self.meta = meta

    def live_games(self, gameday, **kw):
        # The schedule row deliberately carries no team names: the real ones
        # live in the payload's meta, and that is what must reach playsport.
        return [{"GameSno": 309, "Year": "2026", "KindCode": "A",
                 "_payload": {"meta": self.meta, "rows": [], "status": 2}}]


def test_run_matches_games_using_the_live_payloads_team_names():
    """The names come from ``payload['meta']``, like every other reader here.

    Betting on the schedule row instead would make ``oid_for`` return None for
    every game -- playsport would never be used and the CPBL fallback would
    hide it perfectly.
    """
    meta = {"VisitingTeamName": "味全龍", "HomeTeamName": "中信兄弟"}
    source = PlaysportSource(client=_StubFeedClient(
        {DRAGONS: [_entry(DRAGONS, "龍", "兄弟")]}))
    watcher = Watcher(_MetaClient(meta), RecordingNotifier(),
                      cpbl_source="playsport", playsport=source)
    watcher.run(once=True)
    assert watcher.sources == {309: "playsport"}


def test_one_poll_costs_one_batched_request():
    """The endpoint takes an OID list; asking per game spends the lead time."""
    client = _StubFeedClient({DRAGONS: [_entry(DRAGONS, "龍", "兄弟")],
                              UNI: [_entry(UNI, "雄鷹", "獅")]})
    source = PlaysportSource(client=client)
    day = "2026-09-04"
    other = {"VisitingTeamName": "台鋼雄鷹", "HomeTeamName": "統一7-ELEVEn獅"}
    source.available(_game(), day)
    source.state_for(_game(), day)
    source.available(other, day)
    source.state_for(other, day)
    assert len(client.live_calls) == 1, "four questions, one round trip"
    assert sorted(client.live_calls[0][1]) == sorted([DRAGONS, UNI])


def test_constructing_a_playsport_source_touches_no_network():
    """build_watcher() runs at startup and in every CLI test."""
    source = PlaysportSource()
    assert source.client._session is None
    assert source.client._oid_cache is None


def test_an_empty_snapshot_is_quiet_but_a_populated_one_speaks_up(caplog):
    """Before first pitch every game looks unmatched; that is not news.

    The feed carries nothing at all until a game starts, so an empty snapshot
    must not announce that a game "stays on the official CPBL feed" -- it
    moves over as soon as it starts, because ``source_for`` re-decides every
    cycle. A game missing from a snapshot that *does* carry other games is
    genuinely unidentifiable, and that one is worth saying out loud.
    """
    game = {"VisitingTeamName": "樂天桃猿",
            "HomeTeamName": "富邦悍將"}

    quiet = PlaysportSource(client=_StubFeedClient({}))
    with caplog.at_level(logging.INFO, logger="cpbl_alert.playsport"):
        assert quiet.oid_for(game, "2026-09-05") is None
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == [], (
        "an empty snapshot must not claim the game is unmatched")

    oid = "CPBL_20260905_DRAGONS@BRO_1705"
    populated = PlaysportSource(client=_StubFeedClient(
        {oid: [_entry(oid, "龍", "兄弟")]}))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="cpbl_alert.playsport"):
        assert populated.oid_for(game, "2026-09-05") is None
    assert any("no game matching" in r.getMessage() for r in caplog.records), (
        "a populated snapshot that lacks this game must say so")
