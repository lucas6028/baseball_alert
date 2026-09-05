"""MLB: alerting when a Taiwanese player is on stage.

Three kinds of test here.

The parsing tests run against ``tests/fixtures/mlb_schedule.json``, a real
two-day schedule payload with finals, games in progress and games not yet
started in it -- the shape of that payload is the one thing this feature
cannot control.

``mlb_taiwanese_on_stage.json`` and ``mlb_boxscore.json`` are the moment this
whole file exists for, captured off the live API rather than written: 李灝宇
at the plate against Tarik Skubal, 六下 no outs, runner on first, 1-1, having
gone 0 for 2 on the day. One test takes that payload from raw JSON to the
four lines that would have reached the phone.

Everything else is built by hand, because the sequences that matter -- the
same pitcher still on the mound a poll later, a batter retiring and coming up
again two innings on -- cannot be captured on demand.
"""

import datetime as dt
import io
import json
import os

import pytest

from cpbl_alert import mlb
from cpbl_alert.notifier import (
    BREAK,
    LINE_BUDGET,
    MAX_COLUMNS,
    columns,
    format_mlb_alert,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE = os.path.join(FIXTURES, "mlb_schedule.json")
ON_STAGE = os.path.join(FIXTURES, "mlb_taiwanese_on_stage.json")
BOXSCORE = os.path.join(FIXTURES, "mlb_boxscore.json")
PROBABLES = os.path.join(FIXTURES, "mlb_probable_starters.json")

TENG = {"id": 678906, "fullName": "Kai-Wei Teng"}      # 鄧愷威, pitcher
LEE = {"id": 701678, "fullName": "Hao-Yu Lee"}         # 李灝宇, infielder
SKUBAL = {"id": 669373, "fullName": "Tarik Skubal"}
EDMAN = {"id": 669242, "fullName": "Tommy Edman"}
NOBODY = {"id": 999999, "fullName": "Some Body"}       # fills the on-deck slot


def local_hhmm(minutes_ahead):
    """What the alert should print for a game that far off, in Taipei."""
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes_ahead)
    return when.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%H:%M")


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _games(payload):
    return [g for date in payload["dates"] for g in date["games"]]


@pytest.fixture(scope="session")
def schedule():
    return _games(_load(FIXTURE))


@pytest.fixture(scope="session")
def on_stage():
    return _games(_load(ON_STAGE))


@pytest.fixture(scope="session")
def boxscore():
    return _load(BOXSCORE)


@pytest.fixture(scope="session")
def probables():
    """A real slate captured with the hydrate the client now asks for."""
    return _games(_load(PROBABLES))


def upcoming(probable=TENG, *, minutes=12, pk=9, side="away"):
    """A game that has not started, shaped the way MLB shapes one."""
    starts = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
    teams = {"away": {"team": {"id": 147, "teamName": "Yankees"}, "score": 0},
             "home": {"team": {"id": 137, "teamName": "Giants"}, "score": 0}}
    if probable is not None:
        teams[side]["probablePitcher"] = probable
    return {"gamePk": pk, "gameDate": starts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "officialDate": starts.strftime("%Y-%m-%d"),
            "season": "2026", "status": {"abstractGameState": "Preview"},
            "teams": teams, "linescore": {}}


def game(batter=EDMAN, pitcher=SKUBAL, *, on_deck=None, pk=1, inning=6,
         top=True, outs=1, bases=(), balls=0, strikes=0, away_runs=3,
         home_runs=2, away_id=119, home_id=116, state="Live", order=None,
         date="2026-08-28"):
    """A schedule game, shaped the way MLB shapes one."""
    offense = {"batter": batter, "onDeck": on_deck or NOBODY}
    if order is not None:
        offense["battingOrder"] = order
    for base in bases:
        offense[base] = {"id": 1, "fullName": "A Runner"}
    return {
        "gamePk": pk,
        "officialDate": date,
        "gameDate": "2026-08-28T22:40:00Z",
        "season": "2026",
        "status": {"abstractGameState": state},
        "teams": {"away": {"team": {"id": away_id, "teamName": "Dodgers"},
                           "score": away_runs},
                  "home": {"team": {"id": home_id, "teamName": "Tigers"},
                           "score": home_runs}},
        "linescore": {
            "currentInning": inning, "isTopInning": top, "outs": outs,
            "balls": balls, "strikes": strikes,
            "teams": {"away": {"runs": away_runs}, "home": {"runs": home_runs}},
            "offense": offense,
            "defense": {"pitcher": pitcher},
        },
    }


def watcher(box=None, **kw):
    class _Client(mlb.MlbClient):
        """No network: the boxscore is the only call a fire would make."""

        def __init__(self):
            super().__init__()
            self.boxscore_calls = []

        def boxscore(self, game_pk):
            self.boxscore_calls.append(game_pk)
            return box or {}

    class _Sink:
        def __init__(self):
            self.sent = []

        def send(self, text):
            self.sent.append(text)
            return True

    watch = mlb.TaiwaneseWatcher(_Client(), _Sink(), **kw)
    return watch


# -- the payload, as MLB actually returns it -------------------------------
def test_schedule_spans_two_dates(schedule):
    """A night game in the States is the next morning in Taiwan.

    Both US business dates have to be walked; ``dates[0]`` alone drops half
    of what is on.
    """
    with io.open(FIXTURE, encoding="utf-8") as fh:
        dates = json.load(fh)["dates"]
    assert len(dates) == 2
    assert len(schedule) == sum(len(d["games"]) for d in dates)


def test_live_filters_out_finished_and_unstarted(schedule):
    live = mlb.MlbClient.live(schedule)
    assert live, "the captured payload should contain games in progress"
    assert all(g["status"]["abstractGameState"] == "Live" for g in live)
    assert len(live) < len(schedule)


def test_every_live_game_parses(schedule):
    for raw in mlb.MlbClient.live(schedule):
        state = mlb.state_from_mlb_game(raw)
        if state is None:
            continue                       # between halves; nothing to read
        assert 1 <= state.inning <= 20
        assert 0 <= state.outs <= 2
        assert state.visiting_team and state.home_team
        assert state.pitcher


def test_every_club_has_a_chinese_name(schedule):
    """Asserted over the table, not over the fixture.

    A club missing from :data:`mlb.MLB_TEAMS` would simply not appear in any
    one night's schedule, so checking that the captured payload maps cleanly
    proves nothing about the 30th team. The fixture check is still worth
    keeping -- it is what would catch MLB moving the id.
    """
    assert len(mlb.MLB_TEAMS) == 30
    assert all(name and not name.isascii() for name in mlb.MLB_TEAMS.values())
    seen = {int(g["teams"][side]["team"]["id"])
            for g in schedule for side in ("away", "home")}
    assert seen <= set(mlb.MLB_TEAMS), seen - set(mlb.MLB_TEAMS)


def test_an_unknown_club_falls_back_to_its_short_name():
    """Graceful rather than empty -- 7 columns of English, not 19."""
    assert mlb.team_name({"team": {"id": 999, "teamName": "Dodgers",
                                   "name": "Los Angeles Dodgers"}}) == "Dodgers"
    assert mlb.team_name({}) == ""


# -- parsing ---------------------------------------------------------------
def test_pitcher_comes_from_the_defense():
    """``offense.pitcher`` also exists and is the *batting* team's pitcher."""
    raw = game(pitcher=SKUBAL)
    raw["linescore"]["offense"]["pitcher"] = {"id": 1, "fullName": "Wrong Man"}
    assert mlb.state_from_mlb_game(raw).pitcher == "Skubal"


def test_empty_bases_are_absent_not_empty():
    state = mlb.state_from_mlb_game(game(bases=("second",)))
    assert (state.first, state.second, state.third) == (False, True, False)
    assert state.base_code() == "-2-"


def test_between_halves_is_not_a_situation():
    """With three outs the log names the pair from the half that just ended.

    Reading it would put a matchup on the phone that never happens.
    """
    assert mlb.state_from_mlb_game(game(outs=3)) is None


def test_not_started_is_not_a_situation():
    assert mlb.state_from_mlb_game(game(inning=0)) is None


def test_linescore_runs_win_over_the_schedule_score():
    raw = game(away_runs=4, home_runs=2)
    raw["teams"]["away"]["score"] = 0        # schedule copy lags the linescore
    assert mlb.state_from_mlb_game(raw).visiting_score == 4


def test_surname_keeps_a_generational_suffix():
    assert mlb.surname("Tarik Skubal") == "Skubal"
    assert mlb.surname("Luis Garcia Jr.") == "Garcia Jr."
    assert mlb.surname("") == ""


def test_taiwanese_players_get_their_chinese_name():
    assert mlb.display_name(TENG) == "鄧愷威"
    assert mlb.display_name(SKUBAL) == "Skubal"
    # Unknown id, known name: the offline table still catches him.
    assert mlb.display_name({"id": 0, "fullName": "Chien-Ming Wang"}) == "王建民"


# -- who counts ------------------------------------------------------------
def test_roster_name_and_config_all_count():
    watch = watcher(extra_players=[123, "Some Player"])
    watch.roster = {999: "Called Up Yesterday"}
    assert watch.is_taiwanese({"id": 999, "fullName": "Called Up Yesterday"})
    assert watch.is_taiwanese(TENG)                       # built-in id table
    assert watch.is_taiwanese({"id": 0, "fullName": "Wei-Yin Chen"})   # name table
    assert watch.is_taiwanese({"id": 123, "fullName": "Born Abroad"})  # config id
    assert watch.is_taiwanese({"id": 5, "fullName": "Some Player"})    # config name
    assert not watch.is_taiwanese(SKUBAL)
    assert not watch.is_taiwanese(None)


def test_config_players_may_be_ids_or_names():
    watch = watcher(extra_players=["691907", "Yu Chang", "", None])
    assert watch.extra_ids == {691907}
    assert watch.extra_names == {"Yu Chang"}


# -- the trigger -----------------------------------------------------------
def test_fires_when_a_taiwanese_pitcher_takes_the_mound():
    watch = watcher()
    assert watch.process([game(pitcher=SKUBAL)]) == 0
    assert watch.process([game(pitcher=TENG)]) == 1
    assert "台灣投手登板" in watch.notifier.sent[0]


def test_does_not_fire_again_while_he_is_still_out_there():
    """A pitcher who works three innings is one alert, not fifty."""
    watch = watcher()
    watch.process([game(pitcher=SKUBAL)])
    assert watch.process([game(pitcher=TENG)]) == 1
    for batter in (EDMAN, {"id": 2, "fullName": "Next Man"}, EDMAN):
        assert watch.process([game(pitcher=TENG, batter=batter)]) == 0
    assert len(watch.notifier.sent) == 1


def test_a_second_stint_fires_again():
    watch = watcher()
    watch.process([game(pitcher=TENG)])
    watch.process([game(pitcher=SKUBAL)])          # pulled
    assert watch.process([game(pitcher=TENG)]) == 1   # back on (or a new game)


def test_each_plate_appearance_is_one_alert():
    watch = watcher()
    watch.process([game(batter=EDMAN)])
    assert watch.process([game(batter=LEE, strikes=0)]) == 1
    assert watch.process([game(batter=LEE, strikes=2)]) == 0     # same at-bat
    assert watch.process([game(batter=EDMAN)]) == 0
    assert watch.process([game(batter=LEE)]) == 1                # up again
    assert len(watch.notifier.sent) == 2


def test_the_alert_comes_while_he_is_still_on_deck():
    """The whole point of the change.

    A plate appearance is over in two or three minutes, so an alert sent as
    it starts arrives too late to turn a television on for. MLB puts
    ``onDeck`` in the same payload as ``batter``, so the warning costs
    nothing and buys a whole trip to the plate.
    """
    watch = watcher()
    assert watch.process([game(batter=EDMAN, on_deck=LEE)]) == 1
    assert "台灣打者下一棒 李灝宇" in watch.notifier.sent[0]
    assert "打者 Edman" in watch.notifier.sent[0], "line two is still the truth"


def test_stepping_in_is_not_a_second_alert():
    """On deck and then at the plate is one appearance, so it is one buzz."""
    watch = watcher()
    assert watch.process([game(batter=EDMAN, on_deck=LEE)]) == 1
    assert watch.process([game(batter=LEE, on_deck=EDMAN)]) == 0
    assert watch.process([game(batter=LEE, strikes=2, on_deck=EDMAN)]) == 0
    assert len(watch.notifier.sent) == 1


def test_a_man_never_seen_on_deck_still_fires_at_the_plate():
    """Late beats never: a poll can miss the on-deck slot entirely."""
    watch = watcher()
    watch.process([game(batter=EDMAN)])
    assert watch.process([game(batter=LEE)]) == 1
    assert "台灣打者上場" in watch.notifier.sent[-1]


def test_leaving_the_window_and_coming_back_is_a_new_appearance():
    watch = watcher()
    assert watch.process([game(batter=EDMAN, on_deck=LEE)]) == 1
    assert watch.process([game(batter=LEE)]) == 0            # his at-bat
    assert watch.process([game(batter=EDMAN)]) == 0          # done, out of view
    assert watch.process([game(batter=EDMAN, on_deck=LEE)]) == 1   # up again


def test_on_deck_at_two_out_survives_the_change_of_innings():
    """He was announced from the on-deck slot; leading off is not a repeat."""
    watch = watcher()
    assert watch.process([game(batter=EDMAN, on_deck=LEE, outs=2)]) == 1
    assert watch.process([game(batter=LEE, outs=3)]) == 0     # the swap
    assert watch.process([game(batter=LEE, outs=0)]) == 0     # leading off
    assert len(watch.notifier.sent) == 1


def test_the_first_look_at_a_game_is_not_silent():
    """Unlike the CPBL watcher's prime.

    A Taiwanese pitcher standing on the mound right now is the present
    tense, and it is the whole reason this exists -- so attaching mid-game
    announces him rather than swallowing him.
    """
    watch = watcher()
    assert watch.process([game(pitcher=TENG)]) == 1


def test_a_finished_game_never_fires():
    """Its linescore still names the last batter and pitcher of the night."""
    watch = watcher()
    assert watch.process([game(pitcher=TENG, state="Final")]) == 0
    assert watch.process([game(pitcher=TENG, state="Preview")]) == 0


def test_between_halves_does_not_fire_and_does_not_lose_him():
    watch = watcher()
    watch.process([game(batter=EDMAN)])
    assert watch.process([game(batter=LEE, outs=3)]) == 0    # mid-swap: skipped
    assert watch.process([game(batter=LEE, outs=0)]) == 1    # really up now


def test_a_finished_game_forgets_its_stage():
    """A suspended game that resumes should read as a fresh arrival."""
    watch = watcher()
    watch.process([game(pitcher=TENG)])
    watch.process([game(pitcher=TENG, state="Final")])
    assert watch.stages == {}
    assert watch.process([game(pitcher=TENG)]) == 1


def test_a_real_at_bat_end_to_end(on_stage, boxscore):
    """The captured moment, from raw payload to the four lines on the phone.

    李灝宇 up against Tarik Skubal, 六下 no outs, runner on first, 1-1, 0 for 2
    on the day -- 2026-08-29, caught by polling the live API until it
    happened. Thirteen other games were in progress in the same payload and
    none of them says anything.
    """
    watch = watcher(box=boxscore)
    assert watch.process(on_stage) == 1
    assert watch.notifier.sent[0].splitlines() == [
        "道奇 <b>1-1</b> 老虎　六下・無人出局",
        "　◇　　打者 李灝宇",
        "◇　◆　投手 Skubal",
        "<b>台灣打者上場</b>　今日 0-2",
    ]


def test_countryman_against_countryman_is_one_notification():
    watch = watcher()
    watch.process([game(batter=EDMAN, pitcher=SKUBAL)])
    assert watch.process([game(batter=LEE, pitcher=TENG)]) == 1
    assert "台灣內戰" in watch.notifier.sent[0]


def test_a_duel_repeats_only_when_the_pairing_really_changes():
    """The pair is tracked through both roles, not through a "was a duel" flag.

    Standing still is silent; either man being replaced by another Taiwanese
    player is a different matchup and earns its own line.
    """
    other = {"id": 0, "fullName": "Wei-Yin Chen"}
    watch = watcher()
    fired = [watch.process([game(**kw)]) for kw in (
        dict(batter=EDMAN, pitcher=TENG),      # pitcher alert
        dict(batter=LEE, pitcher=TENG),        # he arrives -> duel
        dict(batter=LEE, pitcher=TENG),        # nobody moved
        dict(batter=LEE, pitcher=other),       # new pitcher, new duel
        dict(batter=LEE, pitcher=other),       # nobody moved
        dict(batter=EDMAN, pitcher=other),     # batter gone, pitcher unchanged
        dict(batter=EDMAN, pitcher=SKUBAL),    # nobody Taiwanese left
    )]
    assert fired == [1, 1, 0, 1, 0, 0, 0]
    assert [t.splitlines()[3] for t in watch.notifier.sent] == [
        "<b>台灣投手登板</b>", "<b>台灣內戰</b>", "<b>台灣內戰</b>"]


def test_an_announced_batter_stepping_in_is_not_a_third_notification():
    """The window trigger opened a path the duel rule has to close.

    A Taiwanese pitcher takes the mound while a Taiwanese batter is on deck:
    both are announced, because they are two men and neither is facing the
    other yet. When he steps in, nothing new has happened -- and 台灣內戰
    there would be a third buzz about the same two people.
    """
    watch = watcher()
    fired = [watch.process([game(**kw)]) for kw in (
        dict(batter=EDMAN, pitcher=SKUBAL, on_deck=LEE),   # he is next
        dict(batter=EDMAN, pitcher=TENG, on_deck=LEE),     # our arm comes in
        dict(batter=LEE, pitcher=TENG, on_deck=EDMAN),     # he steps in
        dict(batter=LEE, pitcher=TENG, on_deck=EDMAN),     # nobody moved
    )]
    assert fired == [1, 1, 0, 0]
    assert [t.splitlines()[3] for t in watch.notifier.sent] == [
        "<b>台灣打者下一棒 李灝宇</b>", "<b>台灣投手登板</b>"]


def test_a_new_arm_against_the_same_batter_is_still_a_duel():
    """Even one already announced: the matchup is new, and it is the news."""
    watch = watcher()
    watch.process([game(batter=EDMAN, pitcher=SKUBAL, on_deck=LEE)])
    watch.process([game(batter=LEE, pitcher=SKUBAL)])
    assert watch.process([game(batter=LEE, pitcher=TENG)]) == 1
    assert watch.notifier.sent[-1].splitlines()[3] == "<b>台灣內戰</b>"


def test_a_duel_costs_no_boxscore_request():
    watch = watcher()
    watch.process([game(batter=LEE, pitcher=TENG)])
    assert watch.client.boxscore_calls == []


def test_one_bad_game_does_not_stop_the_others():
    watch = watcher()
    broken = game(pk=2)
    broken["linescore"] = "not a dict"
    assert watch.process([broken, game(pk=3, pitcher=TENG)]) == 1


# -- what line four says ---------------------------------------------------
BATTING = {"plateAppearances": 3, "atBats": 2, "hits": 1, "homeRuns": 0, "rbi": 0}
PITCHING = {"battersFaced": 18, "inningsPitched": "4.2", "strikeOuts": 7,
            "earnedRuns": 0}


def test_batting_detail():
    assert mlb.batting_detail(BATTING) == "今日 1-2"
    assert mlb.batting_detail({**BATTING, "homeRuns": 1, "rbi": 3}) == "今日 1-2・1轟"
    assert mlb.batting_detail({**BATTING, "rbi": 2}) == "今日 1-2・2打點"
    # Before his first trip up there is no line to give.
    assert mlb.batting_detail({"plateAppearances": 0}) == ""


def test_pitching_detail():
    assert mlb.pitching_detail(PITCHING) == "今日 4.2局・7K"
    assert mlb.pitching_detail({**PITCHING, "earnedRuns": 3}) == "今日 4.2局・7K・失3"
    # A reliever who has just been announced has thrown nothing yet.
    assert mlb.pitching_detail({"battersFaced": 0}) == ""


def test_detail_from_boxscore_picks_the_right_side_and_role():
    box = {"teams": {
        "away": {"players": {"ID678906": {"stats": {"pitching": PITCHING}}}},
        "home": {"players": {"ID701678": {"stats": {"batting": BATTING}}}},
    }}
    assert mlb.detail_from_boxscore(box, 678906, "pitcher") == "今日 4.2局・7K"
    assert mlb.detail_from_boxscore(box, 701678, "batter") == "今日 1-2"
    assert mlb.detail_from_boxscore(box, 1, "batter") == ""


def test_the_on_deck_slot_is_one_place_on_from_the_batter():
    """``battingOrder`` is the man at the plate's, and he is not the subject."""
    line = {"offense": {"battingOrder": 8}}
    assert mlb.batting_order_detail(line) == "第八棒"
    assert mlb.batting_order_detail(line, ahead=1) == "第九棒"
    assert mlb.batting_order_detail({"offense": {"battingOrder": 9}},
                                    ahead=1) == "第一棒", "nine wraps to one"


def test_a_batter_with_no_stats_yet_gets_his_slot_in_the_order():
    watch = watcher()
    watch.process([game(batter=EDMAN)])
    watch.process([game(batter=LEE, order=8)])
    assert "第八棒" in watch.notifier.sent[0]


# -- the four lines --------------------------------------------------------
def spotlight(role="batter", detail="今日 1-2"):
    return mlb.Spotlight(role=role, player_id=701678, name="李灝宇", detail=detail)


def test_alert_fits_the_measured_budget():
    """The same budget the CPBL alert is held to -- it is the same phone.

    Team names are Chinese and player names are surnames precisely so this
    passes; a long English club name would blow line one on its own.
    """
    state = mlb.state_from_mlb_game(
        game(batter=LEE, pitcher={"id": 1, "fullName": "Michael Kopech-Longname"},
             bases=("first", "second", "third"), away_id=114, home_id=109))
    # 下一棒 is the line that can overflow: it spends the label, a name and a
    # stat line, where every other role spends two of the three. A romanized
    # surname is twice the width of a Chinese one, which is the worst case.
    cases = [("batter", "今日 1-2・1轟"), ("pitcher", "今日 4.2局・7K・失3"),
             ("duel", ""), ("batter", "第八棒"),
             ("on_deck", "今日 1-2・1轟"), ("on_deck", "第九棒")]
    for role, detail in cases:
        for name in ("李灝宇", "Mountcastle"):
            spot = mlb.Spotlight(role=role, player_id=1, name=name,
                                 detail=detail)
            lines = format_mlb_alert(state, spot).splitlines()
            assert len(lines) == LINE_BUDGET
            for line in lines:
                stripped = line.replace("<b>", "").replace("</b>", "")
                assert columns(stripped) <= MAX_COLUMNS, (stripped,
                                                          columns(stripped))


def test_alert_says_which_role_and_how_his_day_has_gone():
    state = mlb.state_from_mlb_game(game(batter=LEE, bases=("second",)))
    text = format_mlb_alert(state, spotlight("batter", "今日 1-2"))
    lines = text.splitlines()
    assert "道奇" in lines[0] and "老虎" in lines[0] and "六上" in lines[0]
    assert lines[1].endswith("打者 李灝宇")
    assert lines[2].endswith("投手 Skubal")
    assert lines[3] == f"<b>台灣打者上場</b>{BREAK}今日 1-2"


def test_alert_drops_the_break_when_there_is_nothing_after_it():
    """At most one major break per line, and never a trailing one."""
    state = mlb.state_from_mlb_game(game(pitcher=TENG))
    text = format_mlb_alert(state, spotlight("pitcher", ""))
    assert text.splitlines()[3] == "<b>台灣投手登板</b>"
    assert BREAK not in text.splitlines()[3]


def test_the_diamond_is_the_cpbl_diamond():
    """Lines two and three are shared with the CPBL alert, not re-drawn."""
    from cpbl_alert.notifier import diamond_rows

    state = mlb.state_from_mlb_game(game(bases=("first", "third")))
    top, bottom = diamond_rows(state)
    lines = format_mlb_alert(state, spotlight()).splitlines()
    assert lines[1].startswith(top) and lines[2].startswith(bottom)


# -- the pre-game starter notice -------------------------------------------
# The pitcher could not be given the batter's head start: nobody publishes a
# bullpen warming up, and he does not need one -- a reliever is named at the
# change and then faces at least one batter. A *starter* is the exception,
# because he is knowable hours ahead.
def test_the_payload_really_carries_a_probable_starter(probables):
    """Captured with the hydrate the client asks for, not assumed."""
    named = [g for g in probables
             if (g["teams"]["away"].get("probablePitcher")
                 or g["teams"]["home"].get("probablePitcher"))]
    assert len(named) == len(probables) > 20
    one = named[0]["teams"]["away"]["probablePitcher"]
    assert one.get("id") and one.get("fullName")


def test_upcoming_is_bounded_at_both_ends(probables):
    """A game two days out is not news; one already started is not upcoming."""
    now = dt.datetime(2026, 9, 1, 16, 0, tzinfo=dt.timezone.utc)
    within = mlb.MlbClient.upcoming(probables, within=24 * 60, now=now)
    assert within, "the captured slate has games ahead of that moment"
    assert all(mlb.parse_game_time(g["gameDate"]) >= now for g in within)
    assert mlb.MlbClient.upcoming(probables, within=0, now=now) == []


def test_a_taiwanese_starter_is_announced_before_first_pitch():
    watch = watcher()
    assert watch.process([upcoming(TENG)]) == 1
    assert watch.notifier.sent[0].splitlines() == [
        "洋基 @ 巨人" + BREAK + local_hhmm(12) + " 開賽",
        "<b>台灣投手先發 鄧愷威</b>",
    ]
    assert watch.process([upcoming(TENG)]) == 0, "said once"


def test_somebody_else_starting_is_not_news():
    watch = watcher()
    assert watch.process([upcoming(SKUBAL)]) == 0
    assert watch.process([upcoming(None)]) == 0


def test_the_notice_replaces_his_on_the_mound_alert():
    """Walking out to start the game he was announced for is the same event."""
    watch = watcher()
    tonight = upcoming(TENG)
    day = mlb.gameday(tonight)
    assert watch.process([tonight]) == 1
    assert watch.process([game(pitcher=TENG, pk=9, inning=1, date=day)]) == 0
    assert len(watch.notifier.sent) == 1


def test_it_only_replaces_that_one_appearance():
    watch = watcher()
    tonight = upcoming(TENG)
    day = mlb.gameday(tonight)
    watch.process([tonight])
    watch.process([game(pitcher=TENG, pk=9, inning=1, date=day)])
    watch.process([game(pitcher=SKUBAL, pk=9, inning=5, date=day)])   # pulled
    assert watch.process([game(pitcher=TENG, pk=9, inning=6, date=day)]) == 1
    assert watch.notifier.sent[-1].splitlines()[3] == "<b>台灣投手登板</b>"


def test_a_start_that_never_happens_does_not_silence_tomorrow():
    """Rained off, so the discard never runs -- and he starts again next day."""
    watch = watcher()
    assert watch.process([upcoming(TENG, minutes=12)]) == 1
    tomorrow = upcoming(TENG, minutes=12 + 24 * 60)
    tomorrow["status"]["abstractGameState"] = "Preview"
    assert watch.client.upcoming([tomorrow], within=24 * 60 + 30)
    assert watch.process([tomorrow]) == 0, "not due for another day"
    assert watch._process_upcoming(tomorrow) == 1, "and then it speaks"


def test_a_finished_game_is_not_upcoming_whatever_its_start_time_says():
    """A doubleheader's first game is Final while the second is still ahead."""
    done = upcoming(TENG)
    done["status"]["abstractGameState"] = "Final"
    assert mlb.MlbClient.upcoming([done]) == []
    watch = watcher()
    assert watch.process([done]) == 0


def test_both_clubs_probable_starters_are_read():
    """Either side's could be ours, and if both are that is two men."""
    other = {"id": 691907, "fullName": "Tsung-Che Cheng"}
    both = upcoming(TENG, side="away")
    both["teams"]["home"]["probablePitcher"] = other
    watch = watcher()
    assert watch.process([both]) == 2


# -- the polling window ----------------------------------------------------
def test_window_is_two_us_business_dates_wide():
    import datetime as dt

    now = dt.datetime(2026, 8, 29, 0, 15, tzinfo=dt.timezone.utc)
    assert mlb.window(now) == ("2026-08-28", "2026-08-29")


def test_idle_until_something_is_close_to_starting():
    import datetime as dt

    watch = watcher(poll_seconds=20)
    now = dt.datetime.now(dt.timezone.utc)
    soon = (now + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (now + dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert watch._sleep_for([], []) == mlb.IDLE_SLEEP
    assert watch._sleep_for([{"gameDate": later}], []) == mlb.IDLE_SLEEP
    assert watch._sleep_for([{"gameDate": soon}], []) == 20
    assert watch._sleep_for([{"gameDate": later}], [game()]) == 20


def test_poll_interval_has_a_floor():
    assert watcher(poll_seconds=1).poll_seconds == mlb.MIN_POLL_SECONDS
