"""The polling loop, with the network stubbed out."""

from cpbl_alert.watcher import Watcher


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class StubClient:
    """Serves a canned payload; counts how often it is asked."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def game_live(self, sno, year, kind_code="A"):
        self.calls += 1
        return self.payload

    def live_games(self, gameday, **kw):
        return [{"GameSno": 290, "Year": "2026", "KindCode": "A",
                 "GameStatus": 2, "_payload": self.payload}]


def _payload(game290, upto=None):
    rows = game290["rows"][:upto] if upto else game290["rows"]
    return {"meta": game290["meta"], "rows": rows, "scoreboard": [], "status": 2}


def test_priming_suppresses_history(game290):
    """Attaching to a game already in the 9th must not replay old rallies."""
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier)
    w.prime(_payload(game290), 290)
    assert w.process(_payload(game290), 290) == 0
    assert notifier.sent == []


def test_alerts_fire_for_pitches_arriving_after_priming(game290):
    """Prime on the first half of the game, then let the rest arrive."""
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier)
    w.prime(_payload(game290, upto=150), 290)
    fired = w.process(_payload(game290), 290)
    assert fired >= 1
    assert len(notifier.sent) == fired
    assert "滿壘" in "".join(notifier.sent), "the 9th-inning rally should be reported"


def test_repolling_is_silent(game290):
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier)
    w.prime(_payload(game290, upto=150), 290)
    first = w.process(_payload(game290), 290)
    second = w.process(_payload(game290), 290)
    assert first >= 1 and second == 0


def test_team_filter_suppresses_other_games(game290):
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier, teams=["中信兄弟"])
    assert w.process(_payload(game290), 290) == 0, "game290 has no 中信兄弟"
    assert notifier.sent == []


def test_team_filter_does_not_consume_tracker_state(game290):
    """A filtered-out game must not touch the tracker.

    The filter used to run *after* should_fire(), which already records the
    Pkno and arms the rally -- so the game was silently burned through even
    though nothing was sent.
    """
    w = Watcher(StubClient(_payload(game290)), RecordingNotifier(), teams=["中信兄弟"])
    w.process(_payload(game290), 290)
    tracker = w.trackers.get(290)
    assert tracker is None or (not tracker.seen_pknos and tracker.rally_half is None)


def test_team_filter_passes_matching_game(game290):
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier, teams=["台鋼雄鷹"])
    w.prime(_payload(game290, upto=150), 290)
    assert w.process(_payload(game290), 290) >= 1


def test_dry_run_does_not_notify(game290, capsys):
    notifier = RecordingNotifier()
    w = Watcher(StubClient(_payload(game290)), notifier, dry_run=True)
    w.prime(_payload(game290, upto=150), 290)
    w.process(_payload(game290), 290)
    assert notifier.sent == []
    assert capsys.readouterr().out.strip(), "dry run should print instead"


def test_run_once_reuses_the_payload_from_discovery(game290):
    """live_games already fetched the payload; the loop must not refetch.

    The first pass only primes and returns, so this asserts across a second
    pass -- where the payload is actually processed.
    """
    client = StubClient(_payload(game290))
    w = Watcher(client, RecordingNotifier())
    w.run(once=True)          # primes
    w.run(once=True)          # processes
    assert client.calls == 0, "run() should reuse the payload live_games returned"


def test_poll_interval_is_floored():
    """Politeness: never poll someone else's site faster than the floor."""
    w = Watcher(StubClient({}), RecordingNotifier(), poll_seconds=1)
    assert w.poll_seconds >= 10
