"""The leverage model, including the score-margin gate."""

import pytest

from cpbl_alert.leverage import RUN_EXPECTANCY, assess
from cpbl_alert.models import GameState


def s(**kw):
    base = dict(
        game_sno=1, year="2026", kind_code="A", inning=1, is_top=True, outs=0,
        first=False, second=False, third=False, balls=0, strikes=0,
        visiting_score=0, home_score=0, batter="", pitcher="", event_no="x",
        created_at="", visiting_team="V", home_team="H",
    )
    base.update(kw)
    return GameState(**base)


def test_run_expectancy_is_monotonic_in_outs():
    for code, values in RUN_EXPECTANCY.items():
        assert values[0] > values[1] > values[2], f"{code} not decreasing in outs"


def test_more_runners_is_more_dangerous():
    for outs in range(3):
        assert RUN_EXPECTANCY["---"][outs] < RUN_EXPECTANCY["1--"][outs]
        assert RUN_EXPECTANCY["1--"][outs] < RUN_EXPECTANCY["-2-"][outs]
        assert RUN_EXPECTANCY["12-"][outs] < RUN_EXPECTANCY["123"][outs]


# -- the user's explicit requirement ---------------------------------------

def test_blowout_suppresses_scoring_position():
    """RISP in a blowout must NOT alert; the same situation in a close game must."""
    close = assess(s(inning=8, second=True, third=True, visiting_score=3, home_score=4))
    blowout = assess(s(inning=8, second=True, third=True, visiting_score=1, home_score=11))
    assert close.should_alert
    assert not blowout.should_alert
    assert blowout.tension < close.tension / 2


def test_tension_decreases_monotonically_as_deficit_grows():
    tensions = [
        assess(s(inning=8, second=True, visiting_score=5 - d, home_score=5)).tension
        for d in range(0, 8)
    ]
    assert tensions == sorted(tensions, reverse=True), tensions


def test_tying_run_on_base_beats_tying_run_on_deck():
    on_base = assess(s(inning=9, first=True, second=True, visiting_score=3, home_score=5))
    on_deck = assess(s(inning=9, first=True, second=True, visiting_score=1, home_score=5))
    assert on_base.tension > on_deck.tension
    assert "追平分已在壘上" in " ".join(on_base.reasons)


def test_big_lead_damps_but_never_zero():
    a = assess(s(inning=7, second=True, visiting_score=12, home_score=0))
    assert 0 < a.tension < 30
    assert not a.should_alert


# -- inning / situation behaviour ------------------------------------------

def test_late_innings_amplify():
    early = assess(s(inning=2, second=True, third=True))
    late = assess(s(inning=9, second=True, third=True))
    assert late.tension > early.tension


def test_bases_loaded_late_and_close_is_top_tier():
    a = assess(s(inning=9, first=True, second=True, third=True, outs=1,
                 visiting_score=4, home_score=5))
    assert a.should_alert and a.tension > 80
    assert "滿壘" in a.reasons


def test_empty_bases_early_is_quiet():
    a = assess(s(inning=1))
    assert a.tier == "quiet" and not a.should_alert


def test_two_outs_reduces_tension():
    assert (assess(s(inning=8, second=True, outs=0)).tension
            > assess(s(inning=8, second=True, outs=2)).tension)


@pytest.mark.parametrize("inning", [1, 5, 9, 12])
def test_tension_always_bounded(inning):
    for code in RUN_EXPECTANCY:
        a = assess(s(inning=inning, first="1" in code, second="2" in code, third="3" in code))
        assert 0.0 <= a.tension <= 100.0


def test_extra_innings_treated_as_ninth():
    assert (assess(s(inning=12, second=True)).tension
            == assess(s(inning=9, second=True)).tension)


# -- the one-run blend ------------------------------------------------------

def test_late_tie_game_with_runner_on_second_alerts():
    """Regression: a 0-0 game in the 8th with the winning run on 2nd and
    nobody out scored 53.7 under a pure expected-runs model and was missed.
    Real game #288 (樂天桃猿 0-2 中信兄弟) turned on exactly this situation."""
    a = assess(s(inning=8, is_top=False, second=True, outs=0,
                 visiting_score=0, home_score=0))
    assert a.should_alert, f"tie-game 8th-inning RISP must alert (got {a.tension})"


def test_one_run_table_favours_runner_on_third_late():
    """Runner on 3rd with 1 out is modest by expected runs but huge for
    scoring a single run -- late and close, it should outrank a runner on 2nd."""
    third = assess(s(inning=9, third=True, outs=1, visiting_score=4, home_score=5))
    second = assess(s(inning=9, second=True, outs=1, visiting_score=4, home_score=5))
    assert third.tension > second.tension


def test_blowout_ignores_the_one_run_blend():
    """A wide margin must not get the late-and-close treatment."""
    a = assess(s(inning=9, third=True, outs=1, visiting_score=0, home_score=9))
    assert not a.should_alert and a.tension < 25


def test_early_innings_use_expected_runs_not_one_run():
    early = assess(s(inning=1, third=True, outs=1))
    late = assess(s(inning=9, third=True, outs=1))
    assert late.tension > early.tension * 1.5
