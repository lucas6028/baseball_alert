"""Alert throttling, replayed over the real 324-pitch game."""

from cpbl_alert.dedupe import GameTracker
from cpbl_alert.leverage import assess
from cpbl_alert.models import state_from_row


def _fire_sequence(game290, threshold=55.0):
    meta, rows = game290["meta"], game290["rows"]
    tracker = GameTracker()
    fired = []
    for row in rows:
        st = state_from_row(row, meta)
        a = assess(st, threshold=threshold)
        if tracker.should_fire(st, a):
            fired.append((st, a))
    return fired


def test_real_game_produces_few_alerts_not_one_per_pitch(game290):
    fired = _fire_sequence(game290)
    alert_pitches = sum(
        1 for r in game290["rows"]
        if assess(state_from_row(r, game290["meta"])).should_alert
    )
    assert alert_pitches > 10, "fixture should contain a real rally"
    assert 1 <= len(fired) <= 6, f"expected a handful of alerts, got {len(fired)}"
    assert len(fired) < alert_pitches / 3, "dedupe is not collapsing repeated pitches"


def test_alerts_cover_the_ninth_inning_climax(game290):
    fired = _fire_sequence(game290)
    ninth = [(st, a) for st, a in fired if st.inning == 9]
    assert ninth, "the bases-loaded 9th-inning rally must alert"
    st, a = ninth[-1]
    assert st.loaded and a.tension > 80


def test_replaying_the_same_rows_fires_nothing_new(game290):
    """Polling re-sends rows we've already seen; that must be silent."""
    meta, rows = game290["meta"], game290["rows"]
    tracker = GameTracker()
    first = sum(1 for r in rows
                if tracker.should_fire(state_from_row(r, meta),
                                       assess(state_from_row(r, meta))))
    second = sum(1 for r in rows
                 if tracker.should_fire(state_from_row(r, meta),
                                        assess(state_from_row(r, meta))))
    assert first >= 1
    assert second == 0, "re-polling must not re-alert"


def test_scorer_edit_does_not_realert(game290):
    """Rows can be edited after the fact; the Pkno watermark ignores them."""
    meta, rows = game290["meta"], game290["rows"]
    tracker = GameTracker()
    for r in rows:
        st = state_from_row(r, meta)
        tracker.should_fire(st, assess(st))
    edited = dict(rows[-1])
    edited["Content"] = "（更正）"
    st = state_from_row(edited, meta)
    assert not tracker.should_fire(st, assess(st))


def test_new_rally_after_quiet_period_alerts_again(game290):
    fired = _fire_sequence(game290)
    innings = {st.inning for st, _ in fired}
    assert len(innings) >= 2, f"separate rallies should each alert, got innings {innings}"


def test_escalation_within_a_rally_fires_again():
    """A rally that gets materially more dangerous earns a second buzz."""
    from cpbl_alert.models import GameState

    def st(**kw):
        base = dict(game_sno=1, year="2026", kind_code="A", inning=9, is_top=True,
                    outs=0, first=False, second=False, third=False, balls=0, strikes=0,
                    visiting_score=4, home_score=5, batter="", pitcher="", pkno="",
                    created_at="", visiting_team="V", home_team="H")
        base.update(kw)
        return GameState(**base)

    tracker = GameTracker()
    a = st(second=True, pkno="p1")
    b = st(first=True, second=True, third=True, pkno="p2")   # escalates to loaded
    assert tracker.should_fire(a, assess(a))
    assert tracker.should_fire(b, assess(b))


def _st(**kw):
    from cpbl_alert.models import GameState
    base = dict(game_sno=1, year="2026", kind_code="A", inning=9, is_top=True,
                outs=0, first=False, second=False, third=False, balls=0, strikes=0,
                visiting_score=4, home_score=5, batter="", pitcher="", pkno="",
                created_at="", visiting_team="V", home_team="H")
    base.update(kw)
    return GameState(**base)


def test_dip_within_a_half_inning_does_not_rearm():
    """An out mid-rally drops tension below the alert tier. Rebuilding the
    same rally must not buzz again -- only a real escalation should."""
    tracker = GameTracker()
    start = _st(first=True, second=True, outs=0, pkno="p1")
    assert tracker.should_fire(start, assess(start))

    dip = _st(first=True, outs=2, pkno="p2")          # rally stalls
    assert not assess(dip).should_alert
    tracker.should_fire(dip, assess(dip))

    rebuild = _st(first=True, second=True, outs=2, pkno="p3")
    assert not tracker.should_fire(rebuild, assess(rebuild)), "dip must not re-arm"


def test_new_half_inning_rearms():
    tracker = GameTracker()
    a = _st(inning=8, is_top=True, first=True, second=True, third=True, pkno="p1")
    assert tracker.should_fire(a, assess(a))
    b = _st(inning=8, is_top=False, first=True, second=True, third=True, pkno="p2")
    assert tracker.should_fire(b, assess(b)), "a new half-inning is a new rally"
