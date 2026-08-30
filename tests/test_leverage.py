"""The empirical Leverage Index lookup and alert classification."""

import pytest

from cpbl_alert.leverage import DEFAULT_THRESHOLD, assess, leverage_index
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


def test_game_start_is_close_to_the_normalized_average():
    assert leverage_index(s()) == pytest.approx(0.86)


def test_default_threshold_is_twice_average():
    assert DEFAULT_THRESHOLD == 2.0


def test_threshold_classifies_but_does_not_change_li():
    state = s(inning=8, second=True)
    normal = assess(state)
    strict = assess(state, threshold=4.0)
    assert normal.leverage == strict.leverage == pytest.approx(2.89)
    assert normal.should_alert
    assert not strict.should_alert


def test_blowout_is_quiet_while_the_close_state_is_high_leverage():
    close = assess(s(inning=8, second=True, third=True,
                     visiting_score=3, home_score=4))
    blowout = assess(s(inning=8, second=True, third=True,
                       visiting_score=1, home_score=11))
    assert close.should_alert
    assert blowout.leverage == 0.0
    assert not blowout.should_alert


def test_bases_loaded_ninth_inning_comeback_is_extreme():
    a = assess(s(inning=9, first=True, second=True, third=True, outs=1,
                 visiting_score=4, home_score=5))
    assert a.leverage == pytest.approx(7.82)
    assert a.should_alert
    assert "滿壘" in a.reasons
    assert "落後1分" in a.reasons


def test_empty_bases_early_is_quiet():
    a = assess(s())
    assert a.tier == "quiet" and not a.should_alert


def test_late_close_situation_has_more_leverage_than_early_equivalent():
    early = assess(s(inning=1, second=True, third=True))
    late = assess(s(inning=9, second=True, third=True))
    assert late.leverage > early.leverage


def test_walkoff_context_is_side_aware():
    top = assess(s(inning=9, is_top=True, second=True))
    bottom = assess(s(inning=9, is_top=False, second=True))
    assert top.leverage != bottom.leverage
    assert bottom.leverage == pytest.approx(3.12)


def test_extra_innings_use_the_stable_ninth_inning_table():
    assert (assess(s(inning=12, is_top=False, second=True)).leverage
            == assess(s(inning=9, is_top=False, second=True)).leverage)


@pytest.mark.parametrize("margin", [-20, -9, 9, 20])
def test_wide_margins_do_not_generate_sparse_table_false_positives(margin):
    visiting, home = (margin, 0) if margin >= 0 else (0, -margin)
    a = assess(s(inning=9, first=True, second=True, third=True,
                 visiting_score=visiting, home_score=home))
    assert a.leverage == 0.0
    assert not a.should_alert


@pytest.mark.parametrize("margin", [-5, 5])
def test_five_run_margins_keep_the_empirical_value_but_stay_quiet(margin):
    visiting, home = (margin, 0) if margin >= 0 else (0, -margin)
    a = assess(s(inning=9, first=True, second=True, third=True,
                 visiting_score=visiting, home_score=home))
    assert 0.0 < a.leverage < DEFAULT_THRESHOLD
    assert not a.should_alert


def test_runner_on_third_is_more_important_than_second_in_late_one_run_game():
    third = assess(s(inning=9, third=True, outs=1,
                     visiting_score=4, home_score=5))
    second = assess(s(inning=9, second=True, outs=1,
                      visiting_score=4, home_score=5))
    assert third.leverage > second.leverage
