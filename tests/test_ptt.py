"""The PTT voice: shorthand, 推文 marks, and the invariants that keep it safe.

Three groups here are load-bearing rather than cosmetic.

``test_no_html_special...`` guards a silent failure mode: the Telegram send
uses ``parse_mode=HTML``, so a stray angle bracket in a phrase table turns
every alert into a 400 that only shows up in the log.
``test_every_situation_has_a_phrase`` walks the whole base/out/inning space so
a missing table entry is a test failure, not a KeyError at the exact moment
someone wanted an alert.

The third group is the borrowed board slang. Each term in ``ptt._EXTRAS``
encodes one specific game state -- 劇場 is a *closer* in trouble, 開魯閣 is ten
runs allowed, 再見 only exists in a half-inning the batting team can end -- and
a term that escapes its state is the failure the whole table exists to
prevent. Every gate below is tested from both sides: the term must be
reachable where it is true and unreachable where it is not. Scores are not
part of the CRC seed, so reachability is checked by sweeping ``pkno``.
"""

import itertools

import pytest

from cpbl_alert import ptt
from cpbl_alert.leverage import assess
from cpbl_alert.models import GameState, state_from_row
from cpbl_alert.notifier import format_alert


def _state(**kw) -> GameState:
    base = dict(
        game_sno=290, year="2026", kind_code="A", inning=9, is_top=True,
        outs=2, first=True, second=True, third=True, balls=0, strikes=0,
        visiting_score=4, home_score=5, batter="打者", pitcher="投手",
        pkno="1", created_at="", visiting_team="台鋼雄鷹",
        home_team="富邦悍將",
    )
    base.update(kw)
    return GameState(**base)


def _said(**kw) -> str:
    """Everything the board could say about one situation.

    Sweeps ``pkno`` because that is the only field of the CRC seed a test can
    turn freely, so it is how every variant of a slot gets reached.
    """
    out = []
    for pk in map(str, range(40)):
        st = _state(pkno=pk, **kw)
        out.extend(ptt.push_lines(st, assess(st)))
    return " ".join(out)


# -- shorthand -------------------------------------------------------------
def test_teams_use_board_shorthand():
    assert ptt.team("統一7-ELEVEn獅") == "統一"
    assert ptt.team("中信兄弟") == "兄弟"
    assert ptt.team("台鋼雄鷹") == "台鋼"


def test_unknown_team_falls_through_to_its_full_name():
    """Postseason and all-star rosters must not render blank."""
    assert ptt.team("中華隊") == "中華隊"
    assert ptt.team("") == ""


def test_innings_are_written_the_way_the_board_writes_them():
    assert ptt.inning_label(_state(inning=9, is_top=True)) == "九上"
    assert ptt.inning_label(_state(inning=12, is_top=False)) == "十二下"
    assert ptt.outs_label(0) == "無人出局"
    assert ptt.outs_label(2) == "兩出局"


def test_tension_word_tops_out_at_bao():
    """爆 is the board's word for a thread past 100 pushes -- it must be
    reachable, so it bands rather than demanding a literal 100."""
    assert ptt.tension_word(93.0) == "爆"
    assert ptt.tension_word(40.0) != "爆"


# -- the 推文 thread --------------------------------------------------------
def test_push_lines_open_with_the_bases_and_close_with_the_call_to_action():
    """Order, not wording.

    This asserts the opening line is drawn from the *bases* slot rather than
    that it contains one particular word: adding a variant to a slot shifts
    which one the CRC lands on, and an alert that opens with a different but
    equally correct bases line has not regressed.
    """
    st = _state()
    a = assess(st)
    lines = ptt.push_lines(st, a)
    bases = ptt._with_extras("bases", ptt._BASES[st.base_code()], st, a)
    assert lines[0].startswith(f"{ptt.PUSH} ")
    assert lines[0].removeprefix(f"{ptt.PUSH} ") in bases
    assert lines[-1].startswith(ptt.PUSH)
    assert lines[-1].removeprefix(f"{ptt.PUSH} ") in ptt._CTA
    assert all(ln[0] in (ptt.PUSH, ptt.BOO, ptt.ARROW) for ln in lines)


def test_slang_competes_for_a_line_instead_of_adding_one():
    """A lock-screen push has a shape, and more slang must not stretch it.

    Conditional terms are alternatives *within* a slot. If one ever became an
    extra line, a bases-loaded 9th would push the score off a phone preview.
    """
    for code, outs, inning in itertools.product(
            ["---", "1--", "-2-", "--3", "12-", "1-3", "-23", "123"],
            [0, 1, 2], [1, 6, 7, 8, 9, 10]):
        st = _state(inning=inning, outs=outs, first="1" in code,
                    second="2" in code, third="3" in code)
        expected = 5 if inning >= 7 else 4       # the 終盤 line is the fifth
        assert len(ptt.push_lines(st, assess(st))) == expected


def test_a_lead_being_nursed_gets_booed():
    """噓 is the board's reaction to a bullpen protecting a lead."""
    st = _state(visiting_score=7, home_score=4)   # batting team leads by 3
    lines = ptt.push_lines(st, assess(st))
    booed = [ln for ln in lines if ln.startswith(ptt.BOO)]
    assert booed and "領先3分" in booed[0]


def test_slang_still_carries_the_number():
    st = _state(visiting_score=0, home_score=6)   # down 6 with the bases full
    text = " ".join(ptt.push_lines(st, assess(st)))
    assert "落後6分" in text


def test_every_margin_line_keeps_its_figure():
    """The number survives *whichever* variant the CRC picks.

    The two tests above each sample one situation. These two branches are the
    ones with a figure in them, so every variant has to carry it -- a 坐牢 or
    穩了啦 line that dropped the count would still read fine and would quietly
    cost the reader the one fact they wanted.
    """
    for label, st in (("blowout", _state(visiting_score=0, home_score=12)),
                      ("leading", _state(visiting_score=12, home_score=0))):
        a = assess(st)
        assert a.closeness_tag == label
        options = ptt._with_extras("close", ptt._CLOSENESS[label], st, a)
        assert len(options) > len(ptt._CLOSENESS[label])   # extras really ran
        for phrase in options:
            assert "{n}" in phrase, phrase


def test_variant_choice_is_stable_across_runs():
    """A CRC of the situation, not random -- replays must be reproducible."""
    st = _state()
    assert ptt.push_lines(st, assess(st)) == ptt.push_lines(st, assess(st))


def test_conditional_variants_are_ordered_not_hashed():
    """Reproducibility across *processes*, which the test above cannot see.

    ``_with_extras`` builds the option list the CRC indexes into. If it were
    ever built from a set, its order would follow ``PYTHONHASHSEED`` and the
    same pitch would pick a different line tomorrow -- while both calls inside
    one process still agreed with each other.
    """
    assert isinstance(ptt._EXTRAS, tuple)
    for extra in ptt._EXTRAS:
        assert isinstance(extra.lines, tuple), extra.slot
    st = _state()
    assert isinstance(
        ptt._with_extras("close", ptt._CLOSENESS["tying_on_base"], st,
                         assess(st)),
        tuple,
    )


def test_different_situations_do_not_all_sound_the_same():
    a = ptt.push_lines(_state(pkno="1"), assess(_state(pkno="1")))
    b = ptt.push_lines(_state(pkno="2"), assess(_state(pkno="2")))
    assert a != b


# -- the borrowed terms, and the situations that make them true -------------
def test_double_play_talk_needs_a_force_at_second():
    """「別打成雙殺」 with an empty first base is how you tell the board you
    do not actually watch baseball. It must be unreachable there, and
    reachable when the force exists."""
    assert "雙殺" not in _said(outs=1, first=False, second=True, third=False)
    assert "雙殺" in _said(outs=1)


def test_theatre_needs_a_closer_who_is_actually_blowing_a_lead():
    """劇場 is a *closer* in trouble while holding the lead.

    That is the pitching side of ``tying_on_base``, and only from the 9th.
    The same bases-loaded jam in the 7th is a starter in trouble, which the
    board would never call 劇場.
    """
    assert "劇場" in _said(inning=9)          # tying run on base, 9th
    assert "劇場" not in _said(inning=7)
    assert "劇場" not in _said(inning=8)
    # A blowout is not a 劇場 either, however late it gets.
    assert "劇場" not in _said(inning=9, visiting_score=0, home_score=9)


def test_asking_heaven_is_about_your_own_starter_not_the_man_on_the_mound():
    """問天 is a starter who pitched well and got no run support.

    ``state.pitcher`` is the *fielding* team's pitcher, so a line that said
    「投手」 would point at the opposite man. Every 問天 variant has to name
    先發, and the term needs an offence that has actually been silent.
    """
    silent = _said(inning=8, visiting_score=0, home_score=1)
    assert "問天" in silent
    for line in silent.split():
        if "問天" in line:
            assert "先發" in line, line
    # Four runs on the board is not an offence that failed anybody.
    assert "問天" not in _said(inning=8, visiting_score=4, home_score=5)
    # Nor is it the story in the 3rd, before a start is anywhere near wasted.
    assert "問天" not in _said(inning=3, visiting_score=0, home_score=1)


def test_taroko_needs_ten_runs_actually_on_the_board():
    """開魯閣 counts runs allowed, so it is gated on the runs, not the gap."""
    assert "開魯閣" in _said(visiting_score=0, home_score=12)
    assert "開魯閣" not in _said(visiting_score=0, home_score=6)


def test_the_library_needs_the_home_crowd_to_be_the_quiet_one():
    """圖書館 is a *home* stadium gone silent, so the home team must be the
    one losing -- said about a visiting blowout it describes nothing."""
    assert "圖書館" in _said(is_top=False, visiting_score=12, home_score=0)
    assert "圖書館" not in _said(is_top=True, visiting_score=0, home_score=12)


def test_bases_loaded_talk_needs_the_bases_loaded():
    """滿貫砲 and 滿壘大中計 both name a base state; neither may drift off it."""
    loaded = _said()
    assert "滿貫砲" in loaded and "大中計" in loaded
    for kw in ({"first": False}, {"second": False}, {"third": False}):
        partial = _said(**kw)
        assert "滿貫砲" not in partial and "大中計" not in partial


def test_left_on_base_talk_needs_someone_in_scoring_position():
    assert "殘壘" in _said(first=False, second=True, third=False)
    assert "殘壘" not in _said(first=True, second=False, third=False)
    assert "殘壘" not in _said(first=False, second=False, third=False)


def test_walkoff_talk_only_where_the_batting_team_can_end_it():
    """再見 needs a half-inning the batting team can finish. The top of the
    9th or of the 10th is not one, however tense it is."""
    for inning in (9, 10, 12):
        assert "再見" not in _said(inning=inning, is_top=True)
        assert "再見" in _said(inning=inning, is_top=False)


def test_closing_the_door_needs_a_ninth_inning_to_close():
    """關門 is what a closer does with a lead, not what a starter does in the
    6th protecting the same lead."""
    assert "關門" in _said(inning=9, visiting_score=7, home_score=4)
    assert "關門" not in _said(inning=6, visiting_score=7, home_score=4)


def test_home_run_promises_only_where_one_would_actually_tie_it():
    """炸裂 sits on ``tying_at_plate``, where the batter is potential run
    number D and a home run ties the game exactly. Promised anywhere else it
    is arithmetic the board would check."""
    # Bases empty, down 1: the batter himself is the tying run.
    assert "炸裂" in _said(first=False, second=False, third=False,
                           visiting_score=4, home_score=5)
    # Bases loaded, down 1: the tying run is already on base, not at the plate.
    assert "炸裂" not in _said(visiting_score=4, home_score=5)


def test_third_base_lines_hold_with_two_outs():
    """A sac fly or a productive grounder scores nobody with two outs."""
    said = _said(outs=2, first=False, second=False, third=True)
    assert "高飛" not in said and "滾地" not in said


def test_nobody_gets_mocked_by_name():
    """The board's dictionary is full of slang built out of one real player's
    bad night, plus fanbase slurs and umpire-bias accusations. They are real
    slang and they stay out: this ships as a push notification, and the reader
    never opted a named professional into being the punchline.

    Kept deliberately as a list rather than a comment, so the next person
    adding phrases finds the policy where they would trip over it.
    """
    banned = ("國慶球", "國輝球", "玉山大曲", "院長", "坦全", "益全",
              "古亭妹", "北亭妹", "邦寶", "爪寶", "喵寶", "吱寶",
              "邦忙", "識象", "成全", "獅捨", "猿夢", "鋼溫",
              "假球", "放水", "金墊軍", "邦化")
    for phrase in ptt.all_phrases():
        for term in banned:
            assert term not in phrase, f"{term} in {phrase}"


# -- invariants ------------------------------------------------------------
@pytest.mark.parametrize("code,outs,inning,scores", list(itertools.product(
    ["---", "1--", "-2-", "--3", "12-", "1-3", "-23", "123"],
    [0, 1, 2],
    [1, 6, 7, 8, 9, 10],
    # The score axis is what walks _CLOSENESS: tie, one back, far back, ahead.
    [(4, 4), (4, 5), (4, 7), (7, 4)],
)))
def test_every_situation_has_a_phrase(code, outs, inning, scores):
    v, h = scores
    for is_top in (True, False):
        s = _state(inning=inning, outs=outs, is_top=is_top,
                   first="1" in code, second="2" in code, third="3" in code,
                   visiting_score=v, home_score=h)
        assert ptt.push_lines(s, assess(s))


def test_unknown_closeness_tag_degrades_instead_of_crashing():
    """Adding a branch to leverage.py must not be able to kill an alert."""
    import dataclasses
    st = _state()
    a = dataclasses.replace(assess(st), closeness_tag="a_branch_added_later")
    lines = ptt.push_lines(st, a)
    assert lines and any(a.reasons[-1] in ln for ln in lines)


def test_phrase_tables_are_safe_to_render():
    """Sweep every phrase the module owns, not one rendered sample.

    Scores are not part of the CRC seed, so rendering a handful of games
    exercises only a handful of the phrases. Two failure modes hide in the
    rest: an angle bracket makes ``parse_mode=HTML`` return 400 and the alert
    is silently lost, and a literal brace blows up the ``.format(n=...)`` that
    every closeness line goes through -- both at alert time, in the log only.

    This walks ``ptt.all_phrases()`` rather than a list of tables written out
    here, so a table added later cannot skip the sweep by being forgotten.
    """
    phrases = ptt.all_phrases()
    assert len(phrases) > 60
    for phrase in phrases:
        assert not set(phrase) & set("<>&"), phrase
        phrase.format(n=3)


def test_every_slot_is_actually_wired_into_the_alert(monkeypatch):
    """A registered slot that ``push_lines`` never consults is dead phrases.

    ``_EXTRAS`` used to be able to name a slot -- ``late``, ``cta`` -- that the
    renderer read straight from its base table, so the variants rendered
    nowhere, raised nothing, and passed every other test in this file. The
    registry test below checks the phrases are *listed*; this checks they are
    *reachable*.
    """
    consulted = set()
    real = ptt._with_extras

    def spy(slot, base, state, assessment):
        consulted.add(slot)
        return real(slot, base, state, assessment)

    monkeypatch.setattr(ptt, "_with_extras", spy)
    st = _state(inning=9)          # late enough that the 終盤 line exists
    ptt.push_lines(st, assess(st))
    assert consulted == set(ptt._SLOTS)


def test_an_extra_cannot_name_a_slot_nobody_reads():
    """The same guard, enforced at import rather than by remembering to test."""
    assert {e.slot for e in ptt._EXTRAS} <= set(ptt._SLOTS)
    with pytest.raises(ValueError):
        ptt._Extra("nowhere", lambda s, a: True, ("x",))


def test_all_phrases_covers_every_table_the_module_defines():
    """The sweep above is only as good as this registry.

    A new ``_Extra`` is the easy thing to add and the easy thing to leave out
    of ``all_phrases``; this fails when that happens.
    """
    phrases = set(ptt.all_phrases())
    for table in (*ptt._BASES.values(), *ptt._OUTS.values(),
                  *ptt._LATE.values(), *ptt._CLOSENESS.values(), ptt._CTA,
                  ptt._DOUBLE_PLAY):
        assert set(table) <= phrases
    for extra in ptt._EXTRAS:
        assert set(extra.lines) <= phrases, extra.slot


def test_rendered_alert_carries_no_html_beyond_its_own_tags(game290):
    st = state_from_row(game290["rows"][-1], game290["meta"])
    body = format_alert(st, assess(st))
    for tag in ("<b>", "</b>", "<code>", "</code>"):
        body = body.replace(tag, "")
    assert not set(body) & set("<>&")


def test_console_notifier_leaves_no_tags_behind(game290, capsys):
    from cpbl_alert.notifier import ConsoleNotifier
    st = state_from_row(game290["rows"][-1], game290["meta"])
    ConsoleNotifier().send(format_alert(st, assess(st)))
    printed = capsys.readouterr().out
    assert "<" not in printed and ">" not in printed
