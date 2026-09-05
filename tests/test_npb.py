"""NPB: alerting when a Taiwanese player is about to be on stage.

The fixtures here used to be hand-built, and they encoded a page that does
not exist: a ``打者: 外崎`` label beside a live count and a runner list.
npb.jp publishes none of that. It publishes a batting order and a log of
*finished* plate appearances, and the parser that was written against the
imagined page read the string ``カウント`` as the batter's name.

They are captures now -- whole pages, header carousel and all, pulled off
npb.jp during a live evening:

``npb_live.html``
    西武 at ロッテ, second inning, two out, 外崎 at the plate and 林安可 --
    Taiwanese, batting seventh -- on deck. The on-deck alert's fixture, and
    the reason this feature exists: a plate appearance is over in two or
    three minutes.
``npb_live_at_bat.html``
    The same game a half-inning on, with one name in the order table swapped
    for 呉念庭 so that the at-the-plate path has an end-to-end fixture too.
    Real markup, planted name.
``npb_live_change_of_innings.html``
    The same game with the side just retired on a double play -- the banner
    still says 2回裏 and 最新経過 has not opened the next half. What the
    parser makes of it is 3回表, nobody on, nobody out, 林安可 leading off.
``npb_final.html``
    A game that has ended, which publishes no 最新経過 at all.
``npb_schedule_month.html``
    npb.jp's fixture list for the month, which is the only index it will
    serve: ``/scores/<year>/<mmdd>/`` answers 403 to every client.

Both live pages carry the six-game header carousel, which is what keeps
:func:`~cpbl_alert.npb.main_block` honest: every score and inning on the page
appears once for this game and once per game in that strip.
"""

import datetime as dt
import io
import os
import re

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
AT_BAT_PAGE = os.path.join(FIXTURES, "npb_live_at_bat.html")
CHANGE_PAGE = os.path.join(FIXTURES, "npb_live_change_of_innings.html")
FINAL_PAGE = os.path.join(FIXTURES, "npb_final.html")
SCHEDULE = os.path.join(FIXTURES, "npb_schedule_month.html")

LIVE_ID = "2026/0901/m-l-20"
SCHEDULE_PATH = "/games/2026/schedule_09_detail.html"

KU = "古林睿煬"        # 日本ハム, pitcher -- written the same in both scripts
WU = "呉念庭"          # 吳念庭 as npb.jp writes him
LIN = "林安可"         # 西武, batting seventh in the captured page
TONOSAKI = "外崎"      # not Taiwanese; the control in every sequence


def npb_local_hhmm(minutes_ahead):
    """What the alert should print for a game that far off, in Taipei."""
    when = npb.now_jst() + dt.timedelta(minutes=minutes_ahead)
    return when.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%H:%M")


def _read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="session")
def live_page():
    return _read(LIVE_PAGE)


@pytest.fixture(scope="session")
def at_bat_page():
    return _read(AT_BAT_PAGE)


@pytest.fixture(scope="session")
def change_page():
    return _read(CHANGE_PAGE)


@pytest.fixture(scope="session")
def final_page():
    return _read(FINAL_PAGE)


@pytest.fixture(scope="session")
def schedule_page():
    return _read(SCHEDULE)


def game(batter=TONOSAKI, pitcher=KU, *, on_deck="", game_id="2026/0829/f-l-01",
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
        batter=batter, on_deck=on_deck, pitcher=pitcher, batting_order=order,
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
def test_teams_come_off_the_url_code_home_side_first():
    """The slug names the home side first, which reads backwards in English.

    ``f-h-21`` was played at エスコンフィールド, the Fighters' park, so the
    ``f`` is the home side and the alert's visiting team is the ``h``.
    """
    assert npb.teams_from_slug("f-h-21") == ("軟銀", "火腿")
    assert npb.teams_from_slug("db-c-01") == ("廣島", "DeNA")
    assert npb.teams_from_slug("not-a-slug") == ("", "")


def test_printed_names_map_too_long_and_short():
    assert npb.team_name("北海道日本ハムファイターズ") == "火腿"
    assert npb.team_name("日本ハム") == "火腿"
    assert npb.team_name("東北楽天ゴールデンイーグルス") == "樂天"


def test_an_unknown_team_falls_through_rather_than_vanishing():
    assert npb.team_name("侍ジャパン") == "侍ジャパン"


# -- parsing (against real captured pages) ---------------------------------
def test_the_month_page_narrows_to_one_day(schedule_page):
    """A game is linked from its score and from its detail link."""
    day = npb.parse_game_links(schedule_page, "2026-09-01")
    assert sorted(day) == [
        "2026/0901/d-c-20", "2026/0901/e-b-22", "2026/0901/f-h-21",
        "2026/0901/g-db-20", "2026/0901/m-l-20", "2026/0901/s-t-19",
    ]
    # The page is a month wide and links a game once it has started, so the
    # filter is what keeps yesterday's six out of tonight's poll.
    assert npb.parse_game_links(schedule_page, "2026-09-02") == []


def test_live_page_reads_the_whole_situation(live_page):
    g = npb.parse_game_page(live_page, LIVE_ID)
    assert (g.status, g.inning, g.is_top, g.outs) == ("live", 2, True, 2)
    assert (g.away_team, g.home_team) == ("西武", "羅德")
    assert (g.away_score, g.home_score) == (0, 1)
    assert (g.batter, g.on_deck, g.pitcher) == ("外崎", LIN, "高野脩")
    assert g.batting_order == 6


def test_the_header_carousel_is_not_this_game(live_page):
    """Every score and inning on the page is there six times over.

    The strip above the content carries all of the evening's games, and the
    first of them is not this one -- so a rule run over the whole page reads
    somebody else's score. This is the single thing main_block exists for.
    """
    assert "京セラD大阪" in live_page          # another game's ballpark
    assert live_page.count("1回裏") > 1         # and another game's inning
    block = npb.main_block(live_page)
    assert "京セラD大阪" not in block
    assert "ZOZOマリン" in block                # this game's, still there
    assert npb.parse_status(block) == ("live", 2, True)


def test_a_runner_is_read_off_the_base_he_is_standing_on():
    assert npb.bases_cell("1・2塁") == (True, True, False)
    assert npb.bases_cell("3塁") == (False, False, True)
    assert npb.bases_cell("") == (False, False, False)


def test_status_words_beat_a_stale_inning(live_page, final_page):
    """A finished page still says which inning it ended in."""
    assert npb.parse_status(npb.main_block(final_page))[0] == "final"
    assert npb.parse_status(npb.main_block(live_page)) == ("live", 2, True)
    assert npb.parse_status("【中止】")[0] == "called"
    assert npb.parse_status("【試合前】 18:00")[0] == "pregame"
    assert npb.parse_status("nothing here")[0] == "pregame"


def test_a_finished_game_publishes_no_progress_at_all(final_page):
    g = npb.parse_game_page(final_page, "2025/0801/b-f-16")
    assert g.status == "final"
    assert npb.parse_progress(npb.main_block(final_page)) == []
    assert g.batter == "" and g.on_deck == ""


def test_a_missing_field_costs_the_field_not_the_parse():
    g = npb.parse_game_page("<div>【試合中 7回表】</div>", "2026/0829/f-l-01")
    assert g.status == "live" and g.inning == 7
    assert (g.batter, g.on_deck, g.pitcher) == ("", "", "")


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


def test_an_announced_batter_stepping_in_is_not_a_third_notification():
    """The window trigger opened a path the duel rule has to close.

    Two men are announced because they are two men; 台灣內戰 when he steps in
    would be a third buzz about the same pair.
    """
    watch = watcher()
    fired = [watch.process([game(**kw)]) for kw in (
        dict(batter=TONOSAKI, pitcher="平良", on_deck=WU),
        dict(batter=TONOSAKI, pitcher=KU, on_deck=WU),
        dict(batter=WU, pitcher=KU, on_deck=TONOSAKI),
        dict(batter=WU, pitcher=KU, on_deck=TONOSAKI),
    )]
    assert fired == [1, 1, 0, 0]
    assert [t.splitlines()[3] for t in watch.notifier.sent] == [
        "<b>台灣打者下一棒 吳念庭</b>", "<b>台灣投手登板</b>"]


def test_a_new_arm_against_the_same_batter_is_still_a_duel():
    """Even one already announced: the matchup is new, and it is the news."""
    watch = watcher()
    watch.process([game(batter=TONOSAKI, pitcher="平良", on_deck=WU)])
    watch.process([game(batter=WU, pitcher="平良")])
    assert watch.process([game(batter=WU, pitcher=KU)]) == 1
    assert watch.notifier.sent[-1].splitlines()[3] == "<b>台灣內戰</b>"


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
    # 下一棒 is the line that can overflow: it spends the label, a name and a
    # stat line, where every other role spends two of the three.
    for role, detail in (("batter", "打率 .283"), ("pitcher", "投 87球"),
                         ("duel", ""), ("on_deck", "第七棒"),
                         ("on_deck", "打率 .283")):
        for name in (KU, "ワンボーロン"):
            spot = npb.Spotlight(role=role, player_id=None, name=name,
                                 detail=detail)
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
    """The whole path: real HTML in, notification out.

    Two out in the second, 外崎 batting, 林安可 on deck -- so the alert names
    the man who is *not* on line two, which is the one role that has to.
    """
    g = npb.parse_game_page(live_page, LIVE_ID)
    watch = watcher()
    assert watch.process([g]) == 1
    assert watch.notifier.sent[0] == "\n".join((
        "西武 <b>0-1</b> 羅德　二上・兩出局",
        "　◇　　打者 外崎",
        "◇　◇　投手 高野脩",
        "<b>台灣打者下一棒 林安可</b>　第七棒",
    ))


def test_the_man_at_the_plate_is_named_on_line_two(at_bat_page):
    """And then line four says only why, because line two already said who."""
    g = npb.parse_game_page(at_bat_page, LIVE_ID)
    assert g.batter == WU
    watch = watcher()
    assert watch.process([g]) == 1
    assert watch.notifier.sent[0] == "\n".join((
        "西武 <b>0-1</b> 羅德　二下・一出局",
        "　◇　　打者 吳念庭",
        "◇　◆　投手 平良",
        "<b>台灣打者上場</b>　第九棒",
    ))


def test_a_finished_game_reaches_nobody(schedule_page):
    ids = npb.parse_game_links(schedule_page, "2026-09-01")
    watch = watcher()
    assert watch.process([game(game_id=i, status="final") for i in ids]) == 0


# -- the pre-game starter notice -------------------------------------------
# The pitcher could not be given the batter's head start: nobody publishes a
# bullpen warming up, and he does not need one -- a reliever is named at the
# change and then faces at least one batter. A *starter* is the exception,
# because npb.jp announces 予告先発 in the month page's day rows.
def pending(home_starter="", away_starter="", minutes=12):
    when = npb.now_jst() + dt.timedelta(minutes=minutes)
    return npb.NpbGame(game_id="2026/0901/f-h", status="pregame",
                       away_team="軟銀", home_team="火腿", starts_at=when,
                       away_starter=away_starter, home_starter=home_starter)


def test_the_month_page_names_the_probable_starters(schedule_page):
    """Read off the day rows, which is where 予告先発 lives."""
    day = {f.slug: f for f in npb.parse_day_fixtures(schedule_page, "2026-09-01")}
    assert set(day) == {"g-db", "s-t", "d-c", "f-h", "e-b", "m-l"}
    one = day["f-h"]
    assert (one.away_team, one.home_team) == ("軟銀", "火腿")
    assert (one.home_starter, one.away_starter) == ("山﨑福也", "モイネロ")
    assert one.starts_at.hour == 18 and one.starts_at.tzinfo == npb.JST


def test_a_fixture_is_paired_to_its_link_by_team_code(schedule_page):
    """The rows name clubs and the header strip links codes."""
    ids = npb.parse_game_links(schedule_page, "2026-09-01")
    for fixture in npb.parse_day_fixtures(schedule_page, "2026-09-01"):
        assert fixture.under_way(ids), "every game tonight had started"
    assert not npb.Fixture(slug="f-h").under_way([])


def test_a_taiwanese_starter_is_announced_before_first_pitch():
    watch = watcher()
    assert watch.process([pending(home_starter=KU)]) == 1
    assert watch.notifier.sent[0].splitlines() == [
        "軟銀 @ 火腿" + BREAK + npb_local_hhmm(12) + " 開賽",
        "<b>台灣投手先發 古林睿煬</b>",
    ]
    assert watch.process([pending(home_starter=KU)]) == 0, "said once"


def test_no_announcement_is_silent_rather_than_wrong():
    """Whether npb.jp posts 予告先発 before first pitch is unconfirmed."""
    watch = watcher()
    assert watch.process([pending()]) == 0
    assert watch.process([pending(home_starter="山﨑福也")]) == 0


def test_the_notice_replaces_his_on_the_mound_alert():
    watch = watcher()
    assert watch.process([pending(home_starter=KU)]) == 1
    assert watch.process([game(pitcher=KU, game_id="2026/0901/f-h-21")]) == 0
    assert len(watch.notifier.sent) == 1


def test_it_only_replaces_that_one_appearance():
    watch = watcher()
    watch.process([pending(home_starter=KU)])
    watch.process([game(pitcher=KU, game_id="2026/0901/f-h-21")])
    watch.process([game(pitcher="有原", game_id="2026/0901/f-h-21")])
    assert watch.process([game(pitcher=KU, game_id="2026/0901/f-h-21")]) == 1


def test_tomorrow_is_not_news_and_a_start_time_gone_by_is_not_either():
    assert npb.TaiwaneseWatcher.due_soon(pending(minutes=12))
    assert not npb.TaiwaneseWatcher.due_soon(pending(minutes=20 * 60))
    assert not npb.TaiwaneseWatcher.due_soon(pending(minutes=-5))
    assert not npb.TaiwaneseWatcher.due_soon(
        npb.NpbGame(game_id="x", status="pregame"))


def test_both_clubs_probable_starters_are_read():
    watch = watcher()
    assert watch.process([pending(home_starter=KU, away_starter=WU)]) == 2


def test_a_start_that_never_happens_does_not_silence_tomorrow():
    """Rained off, so the discard never runs -- and he starts again next day."""
    watch = watcher()
    assert watch.process([pending(home_starter=KU)]) == 1
    tomorrow = npb.NpbGame(game_id="2026/0902/f-h", status="pregame",
                           away_team="軟銀", home_team="火腿",
                           starts_at=npb.now_jst() + dt.timedelta(minutes=12),
                           home_starter=KU)
    assert watch.process([tomorrow]) == 1


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
    assert (watch._sleep_for([game(status="pregame")], evening)
            == npb.PREGAME_POLL_SECONDS), "the only thing to re-read is 220KB"
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
    client = client_over({SCHEDULE_PATH: "rate limited, try later"})
    with pytest.raises(npb.NpbError):
        client.schedule_page("2026-09-01")


def test_only_the_unsettled_games_are_fetched(schedule_page, live_page):
    pages = {SCHEDULE_PATH: schedule_page,
             "/scores/2026/0901/m-l-20/": live_page,
             "/scores/2026/0901/g-db-20/": "<div>【試合終了】</div>",
             "/scores/2026/0901/s-t-19/": "<div>【中止】</div>",
             "/scores/2026/0901/d-c-20/": "<div>【試合前】 18:00</div>"}
    client = client_over(pages)
    first = {g.game_id: g.status for g in client.games("2026-09-01")}
    assert first["2026/0901/m-l-20"] == "live"
    assert first["2026/0901/g-db-20"] == "final"
    assert first["2026/0901/s-t-19"] == "called"
    assert first["2026/0901/d-c-20"] == "pregame"

    # A finished game does not restart, so the second tick must not ask again.
    client.session.asked.clear()
    second = [g.game_id for g in client.games("2026-09-01")]
    assert "2026/0901/m-l-20" in second and "2026/0901/g-db-20" not in second
    assert "/scores/2026/0901/g-db-20/" not in client.session.asked
    assert "/scores/2026/0901/s-t-19/" not in client.session.asked


def test_one_failing_page_does_not_lose_the_rest(schedule_page, live_page):
    class Flaky(FakeSession):
        def get(self, url, timeout=None):
            if url.endswith("g-db-20/"):
                raise requests.RequestException("boom")
            return super().get(url, timeout)

    client = npb.NpbClient(min_interval=0)
    client.session = Flaky({SCHEDULE_PATH: schedule_page,
                            "/scores/2026/0901/m-l-20/": live_page})
    ids = [g.game_id for g in client.games("2026-09-01")]
    assert "2026/0901/m-l-20" in ids and "2026/0901/g-db-20" not in ids


def test_the_index_is_the_month_page_not_the_day_page():
    """``/scores/<year>/<mmdd>/`` looks right and answers 403 to everyone."""
    client = client_over({})
    try:
        client.schedule_page("2026-09-01")
    except npb.NpbError:
        pass
    assert client.session.asked == ["/games/2026/schedule_09_detail.html"]


def test_the_days_fixtures_are_not_re_read_every_poll(schedule_page, live_page):
    """220KB an index, against six games that change twice an evening."""
    client = client_over({SCHEDULE_PATH: schedule_page,
                          "/scores/2026/0901/m-l-20/": live_page})
    client.game_ids("2026-09-01")
    client.session.asked.clear()
    assert len(client.game_ids("2026-09-01")) == 6
    assert client.session.asked == []


def test_the_fixture_list_still_grows_as_games_start(schedule_page, live_page):
    """The month page links a game only once it is under way.

    So a list taken before the late game started would never come to include
    it, which is why the cache has a life rather than a day.
    """
    client = client_over({SCHEDULE_PATH: schedule_page,
                          "/scores/2026/0901/m-l-20/": live_page})
    client.game_ids("2026-09-01")
    _one_still_to_start(client)
    client.session.asked.clear()
    _age_the_index(client)
    assert len(client.game_ids("2026-09-01")) == 6
    assert client.session.asked == [SCHEDULE_PATH]


def test_once_every_game_has_started_the_index_is_done_with(schedule_page):
    """Nothing it could say next is news, and it is 220KB of not saying it."""
    client = client_over({SCHEDULE_PATH: schedule_page})
    assert len(client.game_ids("2026-09-01")) == 6
    assert client.all_started("2026-09-01")
    client.session.asked.clear()
    _age_the_index(client)
    assert len(client.game_ids("2026-09-01")) == 6
    assert client.session.asked == []


def test_a_game_that_has_not_started_still_keeps_the_poller_awake(schedule_page):
    """An evening before first pitch must not read like one with no baseball.

    The month page lists tonight's six from the moment they are announced and
    links them one by one as they start, so before first pitch there are six
    fixtures and no links at all.
    """
    before = re.sub(r'href="/scores/2026/0901/[^"]*"', 'href="#"', schedule_page)
    client = client_over({SCHEDULE_PATH: before})
    assert client.game_ids("2026-09-01") == []
    assert len(client.fixtures("2026-09-01")) == 6, "listed, just not under way"
    games = client.games("2026-09-01")
    assert [g.status for g in games] == ["pregame"] * 6
    assert not client.all_started("2026-09-01")
    # and each one carries what a starter notice needs
    one = next(g for g in games if g.game_id.endswith("m-l"))
    assert (one.away_team, one.home_team) == ("西武", "羅德")
    assert (one.away_starter, one.home_starter) == ("平良", "高野脩")
    assert one.starts_at.hour == 18


def _age_the_index(client):
    day, ids, listed, when = client._index
    client._index = (day, ids, listed, when - npb.INDEX_TTL - 1)


def _one_still_to_start(client, day="2026-09-01"):
    """Pretend the day lists a seventh game that has not been linked yet."""
    d, ids, listed, when = client._index
    client._index = (d, ids, listed + [npb.Fixture(slug="x-y")], when)


def test_an_empty_refresh_keeps_the_list_it_had(schedule_page):
    """A bad minute on someone else's web server is not a quiet evening."""
    client = client_over({SCHEDULE_PATH: schedule_page})
    first = client.game_ids("2026-09-01")
    _one_still_to_start(client)                # so the index stays in play
    client.session.pages = {}           # a page with no game links in it
    _age_the_index(client)
    assert client.game_ids("2026-09-01") == first
    # and the clock is kept, so it is not re-read every poll from here on
    client.session.asked.clear()
    assert client.game_ids("2026-09-01") == first
    assert client.session.asked == []


def test_a_failed_refresh_keeps_the_list_it_had(schedule_page):
    class Flaky(FakeSession):
        def get(self, url, timeout=None):
            raise requests.RequestException("boom")

    client = client_over({SCHEDULE_PATH: schedule_page})
    first = client.game_ids("2026-09-01")
    _one_still_to_start(client)
    client.session = Flaky({})
    _age_the_index(client)
    assert client.game_ids("2026-09-01") == first


# -- carrying the situation across the play that just ended ----------------
# npb.jp gives the situation a plate appearance *started* in, so the one it is
# in now has to be carried forward. The out count carries exactly; the
# runners do not always, and where they do not the last published bases stand
# rather than an invented set.
def test_the_out_count_carries_exactly():
    assert npb.outs_made("空振り三振") == 1
    assert npb.outs_made("見逃し三振") == 1
    assert npb.outs_made("レフトフライ") == 1
    assert npb.outs_made("ショートゴロ") == 1
    assert npb.outs_made("ショートゴロ併殺打") == 2, "併殺 before ゴロ"
    assert npb.outs_made("センター前ヒット") == 0
    assert npb.outs_made("センター前タイムリーヒット（打点1）") == 0
    assert npb.outs_made("フォアボール") == 0
    assert npb.outs_made("デッドボール") == 0
    assert npb.outs_made("ライト線ツーベース") == 0
    assert npb.outs_made("（走者・大盛）二塁盗塁失敗") == 1


def test_an_unrecognised_result_says_so_rather_than_guessing():
    assert npb.outs_made("珍プレー") is None


def test_the_runners_are_carried_only_where_it_is_certain():
    empty = (False, False, False)
    assert npb.bases_after(empty, "空振り三振") == empty
    assert npb.bases_after(empty, "フォアボール") == (True, False, False)
    assert npb.bases_after((True, False, False), "フォアボール") == (True, True, False)
    assert npb.bases_after((True, True, False), "ホームラン") == empty
    assert npb.bases_after(empty, "ピッチャーゴロ") == empty, "nobody on, nobody left on"
    assert npb.bases_after((True, False, False), "レフトフライ") == (True, False, False)


def test_a_force_play_with_men_on_is_not_guessed():
    """Which runner the ball beat is the whole question, and it is not said."""
    assert npb.bases_after((True, True, False), "ショートゴロ併殺打") is None
    assert npb.bases_after((True, False, False), "（走者・A）二塁盗塁失敗") is None


# -- the change of innings -------------------------------------------------
def test_a_retired_side_rolls_on_to_the_next_half(change_page):
    """The banner lags and 最新経過 has not opened the next half yet.

    npb.jp only starts a half-inning's section once the first plate
    appearance in it is *over*, so without this the man who leads off would
    be the one batter this never announces -- and there is nothing to guess:
    nobody on, nobody out, and the order carrying on.
    """
    assert npb.parse_status(npb.main_block(change_page)) == ("live", 2, False)
    g = npb.parse_game_page(change_page, LIVE_ID)
    assert (g.inning, g.is_top, g.outs) == (3, True, 0)
    assert (g.first, g.second, g.third) == (False, False, False)
    assert (g.batter, g.on_deck) == (LIN, "桑原")
    assert g.batting_order == 7


def test_the_ninth_does_not_roll_into_a_half_nobody_plays():
    assert npb.next_half(7, True, 3, 2) == (7, False)
    assert npb.next_half(7, False, 3, 2) == (8, True)
    assert npb.next_half(9, True, 2, 3) is None, "home side is ahead already"
    assert npb.next_half(9, True, 3, 2) == (9, False), "home side still bats"
    assert npb.next_half(9, False, 3, 2) is None, "somebody won"
    assert npb.next_half(9, False, 3, 3) == (10, True), "tied, so extras"


# -- placing the man ------------------------------------------------------
def test_a_baserunning_event_does_not_move_the_order_on():
    """A caught stealing is logged in the same table and is not a batter.

    It comes with an empty batter cell, so taking "the last row" would leave
    the order stuck one man behind for the rest of the inning.
    """
    batting = npb.Play(outs=1, batter=npb.Player("21325138", "大盛"),
                       result="ライト前ヒット")
    stealing = npb.Play(outs=1, bases=(True, False, False), batter=None,
                        result="（走者・大盛）二塁盗塁失敗")
    half = npb.Half(inning=1, is_top=True, plays=[batting, stealing])
    assert npb.last_batter([half], is_top=True).name == "大盛"


def test_the_order_carries_on_from_the_last_half_this_side_batted():
    """A half-inning that has only just started has nothing logged in it."""
    earlier = npb.Half(inning=1, is_top=True,
                       plays=[npb.Play(batter=npb.Player("1", "名原"))])
    theirs = npb.Half(inning=1, is_top=False,
                      plays=[npb.Play(batter=npb.Player("2", "福永"))])
    assert npb.last_batter([earlier, theirs], is_top=True).name == "名原"
    assert npb.last_batter([earlier, theirs], is_top=False).name == "福永"
    assert npb.last_batter([theirs], is_top=True) is None


def test_the_order_is_read_for_both_leagues(live_page, change_page):
    """A designated hitter bats and the pitcher gets his own unnumbered row;
    without one the pitcher bats ninth and holds a slot like anyone else."""
    away, home = npb.parse_order(npb.main_block(live_page))
    assert away.slots[7].name == LIN
    assert away.pitcher is not None and away.pitcher.name == "平良"
    assert 7 in home.slots and home.pitcher is not None


def test_the_progress_log_is_oldest_half_first(live_page):
    """The page prints the latest half first; nothing downstream should care."""
    halves = npb.parse_progress(npb.main_block(live_page))
    order = [(h.inning, not h.is_top) for h in halves]
    assert order == sorted(order)


# -- the trigger, with the on-deck slot -----------------------------------
def test_the_alert_comes_while_he_is_still_on_deck():
    watch = watcher()
    assert watch.process([game(batter=TONOSAKI, on_deck=WU, pitcher="平良")]) == 1
    assert "台灣打者下一棒 吳念庭" in watch.notifier.sent[0]
    assert "打者 外崎" in watch.notifier.sent[0], "line two is still the truth"


def test_stepping_in_is_not_a_second_alert():
    watch = watcher()
    assert watch.process([game(batter=TONOSAKI, on_deck=WU, pitcher="平良")]) == 1
    assert watch.process([game(batter=WU, on_deck=TONOSAKI, pitcher="平良")]) == 0
    assert len(watch.notifier.sent) == 1


def test_the_on_deck_alert_names_the_slot_he_is_due_in():
    watch = watcher()
    watch.process([game(batter=TONOSAKI, on_deck=WU, pitcher="平良", order=3)])
    assert "第四棒" in watch.notifier.sent[0], "one on from the man at the plate"


def test_a_man_never_seen_on_deck_still_fires_at_the_plate():
    watch = watcher()
    watch.process([game(batter=TONOSAKI, pitcher="平良")])
    assert watch.process([game(batter=WU, pitcher="平良")]) == 1
    assert "台灣打者上場" in watch.notifier.sent[-1]
