"""State parsing, checked against the real captured game."""

from cpbl_alert.models import GameState, state_from_row


def _state(**kw):
    base = dict(
        game_sno=1, year="2026", kind_code="A", inning=1, is_top=True, outs=0,
        first=False, second=False, third=False, balls=0, strikes=0,
        visiting_score=0, home_score=0, batter="", pitcher="", event_no="x",
        created_at="", visiting_team="V", home_team="H",
    )
    base.update(kw)
    return GameState(**base)


def test_base_occupancy_uses_nonempty_string():
    row = {"FirstBase": "3", "SecondBase": "", "ThirdBase": "1", "VisitingHomeType": "1"}
    st = state_from_row(row)
    assert (st.first, st.second, st.third) == (True, False, True)
    assert st.base_code() == "1-3"
    assert st.risp is True
    assert st.loaded is False


def test_scoring_position_and_loaded():
    assert _state(second=True).risp
    assert _state(third=True).risp
    assert not _state(first=True).risp
    assert _state(first=True, second=True, third=True).loaded


def test_batting_side_and_deficit():
    top = _state(is_top=True, visiting_score=2, home_score=5)
    assert top.batting_score == 2 and top.fielding_score == 5
    assert top.deficit == 3          # visiting team trails by 3
    bottom = _state(is_top=False, visiting_score=2, home_score=5)
    assert bottom.deficit == -3      # home team leads by 3
    assert bottom.margin == 3


def test_fixture_parses_and_matches_final_score(game290):
    meta, rows = game290["meta"], game290["rows"]
    states = [state_from_row(r, meta) for r in rows]
    assert len(states) == 324
    last = states[-1]
    assert last.visiting_team == "台鋼雄鷹" and last.home_team == "富邦悍將"
    assert last.inning == 9 and last.is_top
    # Final live-log row: bases loaded, 2 outs, 4-5 going into the last pitch.
    assert last.loaded and last.outs == 2
    assert (last.visiting_score, last.home_score) == (4, 5)


def test_out_count_is_pre_pitch(game290):
    """The documented semantic: an out shows on the NEXT row, not its own.

    This is the off-by-one that would break every threshold if reversed.
    """
    rows = game290["rows"]
    lag = same = 0
    for cur, nxt in zip(rows, rows[1:]):
        if "出局" not in (cur.get("Content") or ""):
            continue
        if (nxt["InningSeq"], nxt["VisitingHomeType"]) != (cur["InningSeq"], cur["VisitingHomeType"]):
            continue
        if nxt["OutCnt"] == cur["OutCnt"] + 1:
            lag += 1
        elif nxt["OutCnt"] == cur["OutCnt"]:
            same += 1
    assert lag > same * 5, f"expected pre-pitch outs, got lag={lag} same={same}"


def test_pitch_id_survives_a_live_log_rebuild():
    """The identity must not move when CPBL regenerates the log.

    Measured against the live endpoint on 2026-08-28: two polls 90 seconds
    apart returned the same 185 pitches with a brand-new ``Pkno`` on every
    row (overlap: zero), and re-fetching the long-finished game 290 gave
    different Pknos than the ones captured in the fixture. Anything keyed on
    Pkno forgets the whole game about once a minute.
    """
    row = {"InningSeq": 6, "VisitingHomeType": "1", "MainEventNo": "0610008000",
           "Pkno": "Z05S8VRX"}
    rebuilt = {**row, "Pkno": "Z05S91AD", "CreateTime": "2026-08-28T20:27:35"}
    assert state_from_row(row).pitch_id == state_from_row(rebuilt).pitch_id


def test_pitch_id_falls_back_to_the_situation():
    """If MainEventNo ever disappears, degrade to the moment, not to a mint."""
    a = _state(event_no="", inning=6, outs=1, second=True, balls=2, strikes=1)
    b = _state(event_no="", inning=6, outs=1, second=True, balls=2, strikes=1)
    c = _state(event_no="", inning=6, outs=1, second=True, balls=3, strikes=1)
    assert a.pitch_id == b.pitch_id
    assert a.pitch_id != c.pitch_id


def test_fixture_rows_carry_a_stable_identity(game290):
    rows = game290["rows"]
    ids = {state_from_row(r).pitch_id for r in rows}
    # 324 pitches, 323 ids: the "比賽結束" marker repeats the final pitch's
    # MainEventNo, and swallowing that row is the behaviour we want.
    assert len(ids) == len(rows) - 1
