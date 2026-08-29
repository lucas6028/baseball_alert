"""NPB: alerting when a Taiwanese player is on stage.

The fixtures here are **hand-built, and that is a real difference from the
rest of this suite.** ``game290.json`` and the MLB payloads are captures --
the shape of a CPBL live log and an MLB schedule is the one thing those
features cannot control, so they are tested against the real thing. npb.jp
was not reachable from where this was written, so ``npb_live.html`` and
``npb_scoreboard.html`` encode what ``cpbl_alert.npb.FIELD_PATTERNS``
*assumes* the page looks like rather than what it does.

So read what these tests prove carefully. Everything downstream of
:class:`~cpbl_alert.npb.NpbGame` -- who counts as Taiwanese, the name
folding, the trigger, the four lines that reach the phone -- is proved here
outright, because none of it depends on the markup. Everything upstream of it
is proved only against the assumption, and ``cpbl-alert npb-probe`` is how
the assumption gets checked against the site.
"""

import io
import os

import pytest
import requests

from cpbl_alert import npb
from cpbl_alert.notifier import (
    BREAK,
    LINE_BUDGET,
    MAX_COLUMNS,
    columns,
    format_stage_alert,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
LIVE_PAGE = os.path.join(FIXTURES, "npb_live.html")
SCOREBOARD = os.path.join(FIXTURES, "npb_scoreboard.html")

KU = "古林睿煬"        # 日本ハム, pitcher -- written the same in both scripts
WU = "呉念庭"          # 吳念庭 as npb.jp writes him
TONOSAKI = "外崎"      # not Taiwanese; the control in every sequence


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="session")
def live_page():
    return _read(LIVE_PAGE)


@pytest.fixture(scope="session")
def scoreboard_page():
    return _read(SCOREBOARD)


def game(batter=TONOSAKI, pitcher=KU, *, game_id="2026/0829/f-l-01",
         status="live", inning=7, top=True, outs=1, bases=(), balls=0,
         strikes=0, away=3, home=2, order=None, batter_detail="",
         pitcher_detail=""):
    """An :class:`NpbGame`, i.e. a page that has already been read."""
    return npb.NpbGame(
        game_id=game_id, status=status,
        away_team="火腿", home_team="西武",
        inning=inning, is_top=top, outs=outs, balls=balls, strikes=strikes,
        away_score=away, home_score=home,
        first="1" in bases, second="2" in bases, third="3" in bases,
        batter=batter, pitcher=pitcher, batting_order=order,
        batter_detail=batter_detail, pitcher_detail=pitcher_detail,
    )


class Sink:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class Feed:
    """A feed that hands out canned batches, one per call."""

    def __init__(self, *batches):
        self.batches = list(batches)
        self.days = []

    def games(self, day):
        self.days.append(day)
        return self.batches.pop(0) if self.batches else []


def watcher(*batches, **kw):
    watch = npb.TaiwaneseWatcher(Feed(*batches), Sink(), **kw)
    return watch


# -- names -----------------------------------------------------------------
def test_shinjitai_folds_to_traditional():
    """呉念庭 on the page is 吳念庭 in the alert. This is the whole point."""
    assert npb.normalize("呉念庭") == "吳念庭"
    assert npb.normalize("吳念庭") == "吳念庭"
    assert npb.display_name("呉念庭") == "吳念庭"


def test_padding_and_middle_dots_are_not_part_of_a_name():
    assert npb.normalize("呉　念庭") == "吳念庭"
    assert npb.normalize("王 柏融") == "王柏融"
    assert npb.normalize("ワン・ボーロン") == "ワンボーロン"


def test_a_name_written_the_same_way_survives_folding():
    assert npb.normalize(KU) == KU
    assert npb.display_name(KU) == KU


def test_unknown_player_keeps_whatever_the_page_printed():
    assert npb.display_name("外崎") == "外崎"
    assert npb.display_name("") == ""


def test_every_roster_key_folds_onto_its_own_display_name():
    """A key that folds to something else is a player who never matches."""
    for written, chinese in npb.TAIWANESE_NPB.items():
        assert npb.normalize(written) == npb.normalize(chinese), written


# -- membership ------------------------------------------------------------
def test_roster_matches_either_orthography():
    watch = watcher()
    assert watch.is_taiwanese("呉念庭")
    assert watch.is_taiwanese("吳念庭")
    assert not watch.is_taiwanese("外崎")
    assert not watch.is_taiwanese("")


def test_config_adds_a_player_the_table_does_not_know():
    """The supported way to add this year's signing without a release."""
    watch = watcher(extra_players=["徐若熙"])
    assert watch.is_taiwanese("徐若熙")
    assert watch.display("徐若熙") == "徐若熙"


def test_config_entry_matches_the_japanese_rendering_too():
    watch = watcher(extra_players=["呂彥青"])
    assert watch.is_taiwanese("呂彦青")


def test_blank_config_entries_are_ignored():
    watch = watcher(extra_players=["", None, "  "])
    assert not watch.is_taiwanese("")
    assert not watch.is_taiwanese(" ")


# -- teams -----------------------------------------------------------------
def test_teams_come_off_the_url_code():
    assert npb.teams_from_slug("f-l-01") == ("火腿", "西武")
    assert npb.teams_from_slug("db-c-01") == ("DeNA", "廣島")
    assert npb.teams_from_slug("not-a-slug") == ("", "")


def test_printed_names_map_too_long_and_short():
    assert npb.team_name("北海道日本ハムファイターズ") == "火腿"
    assert npb.team_name("日本ハム") == "火腿"
    assert npb.team_name("東北楽天ゴールデンイーグルス") == "樂天"


def test_an_unknown_team_falls_through_rather_than_vanishing():
    assert npb.team_name("侍ジャパン") == "侍ジャパン"


# -- parsing (against the assumed page shape) ------------------------------
def test_scoreboard_yields_each_game_once(scoreboard_page):
    """A game is linked from its score, its names and its 詳細 link."""
    assert npb.parse_scoreboard(scoreboard_page) == [
        "2026/0829/f-l-01", "2026/0829/g-t-01",
        "2026/0829/e-b-01", "2026/0829/db-c-01",
    ]


def test_live_page_reads_the_whole_situation(live_page):
    g = npb.parse_game_page(live_page, "2026/0829/f-l-01")
    assert (g.status, g.inning, g.is_top, g.outs) == ("live", 7, True, 1)
    assert (g.balls, g.strikes) == (2, 1)
    assert (g.away_score, g.home_score) == (3, 2)
    assert (g.first, g.second, g.third) == (False, True, False)
    assert (g.batter, g.pitcher) == ("外崎", KU)
    assert g.batting_order == 3
    assert (g.batter_detail, g.pitcher_detail) == ("打率 .283", "投 87球")


def test_a_named_runner_occupies_his_base_and_なし_does_not():
    assert npb.parse_bases("一塁 なし\n二塁 山田\n三塁 ー") == (False, True, False)
    assert npb.parse_bases("一塁 佐藤\n二塁 なし\n三塁 鈴木") == (True, False, True)


def test_status_words_beat_a_stale_inning():
    """A finished page still says which inning it ended in."""
    assert npb.parse_status("9回裏 試合終了") == "final"
    assert npb.parse_status("中止") == "called"
    assert npb.parse_status("試合前 18:00") == "pregame"
    assert npb.parse_status("7回表 1アウト") == "live"


def test_a_date_is_not_a_score():
    """Without the lookarounds, '2026-08-29' reads as an 8-29 rout."""
    assert npb.parse_score("更新 2026-08-29 18:04") == (0, 0)
    assert npb.parse_score("巨人 1 - 5 阪神") == (1, 5)


def test_a_missing_field_costs_the_field_not_the_parse():
    g = npb.parse_game_page("<div>7回表 1アウト 打者 外崎</div>", "2026/0829/f-l-01")
    assert g.status == "live" and g.batter == "外崎"
    assert (g.balls, g.strikes, g.pitcher) == (0, 0, "")


def test_tags_become_line_breaks_so_a_label_does_not_run_into_its_neighbour():
    text = npb.strip_tags("<td>打者</td><td>外崎</td>")
    assert "打者\n外崎" in text


def test_entities_are_unescaped():
    assert "A&B" in npb.strip_tags("<p>A&amp;B</p>")


# -- state -----------------------------------------------------------------
def test_live_game_becomes_a_state():
    st = npb.state_from_npb_game(game(bases="2", outs=1))
    assert st is not None
    assert st.describe() == "7局上 1出局 -2- 3-2"
    assert st.visiting_team == "火腿" and st.home_team == "西武"


def test_the_alert_prints_the_traditional_name():
    st = npb.state_from_npb_game(game(pitcher="呉念庭"))
    assert st.pitcher == "吳念庭"


def test_nothing_to_read_yet_or_any_more():
    assert npb.state_from_npb_game(game(status="pregame")) is None
    assert npb.state_from_npb_game(game(status="final")) is None
    assert npb.state_from_npb_game(game(status="called")) is None
    assert npb.state_from_npb_game(game(inning=0)) is None


def test_the_half_inning_swap_is_skipped():
    """Three outs showing means the page names two different halves' men."""
    assert npb.state_from_npb_game(game(outs=3)) is None


# -- the trigger -----------------------------------------------------------
def test_a_taiwanese_pitcher_on_the_mound_fires_on_the_first_look():
    watch = watcher()
    assert watch.process([game(pitcher=KU)]) == 1
    assert "台灣投手登板" in watch.notifier.sent[0]


def test_he_does_not_fire_again_while_he_is_still_out_there():
    """One relief appearance is one alert, not one per pitch."""
    watch = watcher()
    watch.process([game(pitcher=KU, outs=1)])
    assert watch.process([game(pitcher=KU, outs=2, balls=1)]) == 0
    assert len(watch.notifier.sent) == 1


def test_every_plate_appearance_of_his_fires_once():
    watch = watcher()
    assert watch.process([game(batter=WU, pitcher="今井")]) == 1
    assert watch.process([game(batter="外崎", pitcher="今井")]) == 0
    assert watch.process([game(batter=WU, pitcher="今井", inning=9)]) == 1
    assert len(watch.notifier.sent) == 2


def test_a_game_with_nobody_of_ours_in_it_is_silent():
    watch = watcher()
    assert watch.process([game(batter="外崎", pitcher="今井")]) == 0
    assert watch.notifier.sent == []


def test_a_taiwanese_duel_is_one_notification_not_two():
    watch = watcher()
    assert watch.process([game(batter=WU, pitcher=KU)]) == 1
    assert "台灣內戰" in watch.notifier.sent[0]


def test_a_pitching_change_to_a_taiwanese_arm_fires_mid_at_bat():
    watch = watcher()
    watch.process([game(batter="外崎", pitcher="今井")])
    assert watch.process([game(batter="外崎", pitcher=KU)]) == 1


def test_finishing_a_game_forgets_it():
    """A suspended game resumed later is a fresh arrival, not a continuation."""
    watch = watcher()
    watch.process([game(pitcher=KU)])
    watch.process([game(pitcher=KU, status="final")])
    assert watch.stages == {}
    assert watch.process([game(pitcher=KU)]) == 1


def test_two_games_are_tracked_apart():
    watch = watcher()
    watch.process([game(pitcher=KU),
                   game(game_id="2026/0829/e-b-01", batter=WU, pitcher="山本")])
    assert len(watch.notifier.sent) == 2
    assert watch.process([game(pitcher=KU),
                          game(game_id="2026/0829/e-b-01", batter=WU,
                               pitcher="山本")]) == 0


def test_one_unreadable_game_does_not_silence_the_others():
    watch = watcher()
    broken = game(game_id="2026/0829/g-t-01")
    object.__setattr__(broken, "outs", "not a number")
    assert watch.process([broken, game(pitcher=KU)]) == 1


# -- line four -------------------------------------------------------------
def test_a_pitcher_gets_his_pitch_count():
    watch = watcher()
    watch.process([game(pitcher=KU, pitcher_detail="投 87球")])
    assert "投 87球" in watch.notifier.sent[0]


def test_a_batter_gets_his_average():
    watch = watcher()
    watch.process([game(batter=WU, pitcher="今井", batter_detail="打率 .275")])
    assert "打率 .275" in watch.notifier.sent[0]


def test_before_his_first_trip_up_the_order_slot_stands_in():
    watch = watcher()
    watch.process([game(batter=WU, pitcher="今井", order=8)])
    assert "第八棒" in watch.notifier.sent[0]


def test_with_neither_line_four_is_just_the_reason():
    watch = watcher()
    watch.process([game(batter=WU, pitcher="今井")])
    assert watch.notifier.sent[0].splitlines()[3] == "<b>台灣打者上場</b>"


def test_a_duel_carries_no_stat_line_because_it_would_be_whose():
    watch = watcher()
    watch.process([game(batter=WU, pitcher=KU, batter_detail="打率 .275")])
    assert watch.notifier.sent[0].splitlines()[3] == "<b>台灣內戰</b>"


# -- the phone -------------------------------------------------------------
def test_the_alert_fits_the_measured_budget():
    st = npb.state_from_npb_game(game(bases="23", outs=1))
    for role, detail in (("batter", "打率 .283"), ("pitcher", "投 87球"),
                         ("duel", "")):
        spot = npb.Spotlight(role=role, player_id=None, name=KU, detail=detail)
        lines = format_stage_alert(st, spot).splitlines()
        assert len(lines) == LINE_BUDGET
        for line in lines:
            plain = line.replace("<b>", "").replace("</b>", "")
            assert columns(plain) <= MAX_COLUMNS, plain


def test_the_diamond_rows_start_their_text_in_the_same_column():
    st = npb.state_from_npb_game(game(bases="13"))
    spot = npb.Spotlight(role="pitcher", player_id=None, name=KU)
    two, three = format_stage_alert(st, spot).splitlines()[1:3]
    assert columns(two.split("打者")[0]) == columns(three.split("投手")[0])


def test_at_most_one_major_break_per_line():
    st = npb.state_from_npb_game(game(bases="2"))
    spot = npb.Spotlight(role="pitcher", player_id=None, name=KU,
                         detail="投 87球")
    for line in format_stage_alert(st, spot).splitlines()[3:]:
        assert line.count(BREAK) <= 1


# -- end to end ------------------------------------------------------------
def test_a_page_becomes_the_four_lines_that_reach_the_phone(live_page):
    """The whole path: HTML in, notification out."""
    g = npb.parse_game_page(live_page, "2026/0829/f-l-01")
    watch = watcher()
    assert watch.process([g]) == 1
    assert watch.notifier.sent[0] == "\n".join((
        "火腿 <b>3-2</b> 西武　七上・一出局",
        "　◆　　打者 外崎",
        "◇　◇　投手 古林睿煬",
        "<b>台灣投手登板</b>　投 87球",
    ))


def test_a_finished_game_on_the_scoreboard_reaches_nobody(scoreboard_page):
    ids = npb.parse_scoreboard(scoreboard_page)
    watch = watcher()
    assert watch.process([game(game_id=i, status="final") for i in ids]) == 0


# -- the loop --------------------------------------------------------------
def test_run_once_walks_every_day_in_the_window():
    watch = watcher([game(pitcher=KU)])
    watch.run(once=True)
    assert watch.feed.days == npb.window()
    assert len(watch.notifier.sent) == 1


def test_poll_pace_is_floored():
    assert watcher(poll_seconds=1).poll_seconds == npb.MIN_POLL_SECONDS


def test_a_live_game_is_polled_and_a_dead_evening_is_not():
    watch = watcher()
    evening = npb.now_jst().replace(hour=19)
    assert watch._sleep_for([game()], evening) == watch.poll_seconds
    assert watch._sleep_for([game(status="pregame")], evening) == watch.poll_seconds
    assert watch._sleep_for([game(status="final")], evening) == npb.IDLE_SLEEP
    assert watch._sleep_for([], evening) == npb.IDLE_SLEEP


def test_outside_the_active_hours_it_idles_even_with_games_listed():
    watch = watcher()
    dawn = npb.now_jst().replace(hour=5)
    assert watch._sleep_for([game(status="pregame")], dawn) == npb.IDLE_SLEEP


def test_dry_run_prints_and_does_not_push(capsys):
    watch = watcher(dry_run=True)
    watch.process([game(pitcher=KU)])
    assert "台灣投手登板" in capsys.readouterr().out
    assert watch.notifier.sent == []


# -- the calendar ----------------------------------------------------------
def test_a_normal_evening_is_one_japanese_date():
    import datetime as dt
    evening = dt.datetime(2026, 8, 29, 19, 30, tzinfo=npb.JST)
    assert npb.window(evening) == ["2026-08-29"]


def test_the_small_hours_still_look_at_yesterday():
    """A 12-inning game that ran past midnight is filed under yesterday."""
    import datetime as dt
    late = dt.datetime(2026, 8, 30, 0, 20, tzinfo=npb.JST)
    assert npb.window(late) == ["2026-08-29", "2026-08-30"]


# -- the client's own behaviour -------------------------------------------
class FakeResponse:
    def __init__(self, text, encoding="utf-8"):
        self.text = text
        self.encoding = encoding
        self.apparent_encoding = "utf-8"

    def raise_for_status(self):
        return None


class FakeSession:
    """Serves canned pages and records what was actually asked for."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []
        self.headers = {}

    def get(self, url, timeout=None):
        path = url.replace(npb.BASE, "")
        self.asked.append(path)
        return FakeResponse(self.pages.get(path, "<html></html>"))


def client_over(pages):
    client = npb.NpbClient(min_interval=0)
    client.session = FakeSession(pages)
    return client


def test_a_non_html_body_is_an_error_not_an_empty_game():
    """A CDN interstitial is still a 200, and would parse as 'not live'."""
    client = client_over({"/scores/2026/0829/": "rate limited, try later"})
    with pytest.raises(npb.NpbError):
        client.scoreboard("2026-08-29")


def test_only_the_unsettled_games_are_fetched(scoreboard_page, live_page):
    pages = {"/scores/2026/0829/": scoreboard_page,
             "/scores/2026/0829/f-l-01/": live_page,
             "/scores/2026/0829/g-t-01/": "<div>9回裏 試合終了</div>",
             "/scores/2026/0829/e-b-01/": "<div>中止</div>",
             "/scores/2026/0829/db-c-01/": "<div>試合前 18:00</div>"}
    client = client_over(pages)
    first = client.games("2026-08-29")
    assert [g.status for g in first] == ["live", "final", "called", "pregame"]

    # A finished game does not restart, so the second tick must not ask again.
    client.session.asked.clear()
    second = client.games("2026-08-29")
    assert [g.game_id for g in second] == ["2026/0829/f-l-01",
                                           "2026/0829/db-c-01"]
    assert "/scores/2026/0829/g-t-01/" not in client.session.asked
    assert "/scores/2026/0829/e-b-01/" not in client.session.asked


def test_one_failing_page_does_not_lose_the_rest(scoreboard_page, live_page):
    class Flaky(FakeSession):
        def get(self, url, timeout=None):
            if url.endswith("g-t-01/"):
                raise requests.RequestException("boom")
            return super().get(url, timeout)

    client = npb.NpbClient(min_interval=0)
    client.session = Flaky({"/scores/2026/0829/": scoreboard_page,
                            "/scores/2026/0829/f-l-01/": live_page})
    ids = [g.game_id for g in client.games("2026-08-29")]
    assert "2026/0829/f-l-01" in ids and "2026/0829/g-t-01" not in ids


def test_the_scoreboard_url_is_the_japanese_date():
    client = client_over({})
    try:
        client.scoreboard("2026-08-29")
    except npb.NpbError:
        pass
    assert client.session.asked == ["/scores/2026/0829/"]
