"""State parsing, checked against the real captured game."""

from cpbl_alert.models import GameState, state_from_row


def _state(**kw):
    base = dict(
        game_sno=1, year="2026", kind_code="A", inning=1, is_top=True, outs=0,
        first=False, second=False, third=False, balls=0, strikes=0,
        visiting_score=0, home_score=0, batter="", pitcher="", pkno="x",
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
