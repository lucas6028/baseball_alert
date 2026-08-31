"""The command layer: watcher construction and the all-leagues command."""

import argparse

import pytest

from cpbl_alert import cli, mlb, npb
from cpbl_alert.config import DEFAULTS, LEAGUES
from cpbl_alert.watcher import Watcher


@pytest.fixture
def cfg():
    return dict(DEFAULTS)


def all_args(**kwargs):
    args = {"league": None, "once": True, "dry_run": True}
    args.update(kwargs)
    return argparse.Namespace(**args)


# -- build_watcher ---------------------------------------------------------
def test_build_watcher_picks_the_right_class_per_league(cfg):
    assert isinstance(cli.build_watcher("cpbl", cfg), Watcher)
    assert isinstance(cli.build_watcher("mlb", cfg), mlb.TaiwaneseWatcher)
    assert isinstance(cli.build_watcher("npb", cfg), npb.TaiwaneseWatcher)


def test_build_watcher_keeps_each_league_on_its_own_poll_rate(cfg):
    """The reason ``all`` has no --poll: three sources, three tuned defaults."""
    rates = {lg: cli.build_watcher(lg, cfg).poll_seconds for lg in LEAGUES}
    assert rates == {"cpbl": 15, "mlb": 20, "npb": 30}


def test_build_watcher_applies_cli_overrides(cfg):
    watcher = cli.build_watcher("cpbl", cfg, poll=30, threshold=4.5)
    assert (watcher.poll_seconds, watcher.threshold) == (30, 4.5)


def test_build_watcher_rejects_an_unknown_league(cfg):
    with pytest.raises(ValueError):
        cli.build_watcher("kbo", cfg)


def test_build_watcher_gives_each_league_its_own_channel(cfg):
    """The silent failure this guards: 大聯盟 alerts landing in 中職台.

    A transposed league argument changes nothing observable -- same classes,
    same poll rates, same dry-run output -- so it is asserted on the notifier
    identity directly.
    """
    cfg.update({
        "discord_webhook_cpbl": "https://discord.com/api/webhooks/111/aaa",
        "discord_webhook_mlb": "https://discord.com/api/webhooks/222/bbb",
        "discord_webhook_npb": "https://discord.com/api/webhooks/333/ccc",
    })
    keys = {lg: cli.build_watcher(lg, cfg).notifier.key for lg in LEAGUES}
    assert keys["cpbl"] == ("discord", cfg["discord_webhook_cpbl"])
    assert keys["mlb"] == ("discord", cfg["discord_webhook_mlb"])
    assert keys["npb"] == ("discord", cfg["discord_webhook_npb"])


def test_build_watcher_falls_back_to_the_shared_channel(cfg):
    """Leaving a suffix out puts that league back on the common channel."""
    cfg.update({"discord_webhook": "https://discord.com/api/webhooks/111/aaa",
                "discord_webhook_mlb": "https://discord.com/api/webhooks/222/bbb"})
    keys = {lg: cli.build_watcher(lg, cfg).notifier.key for lg in LEAGUES}
    assert keys["cpbl"] == keys["npb"] != keys["mlb"]


def test_watcher_summary_names_the_league_and_the_destination(cfg):
    summary = cli.watcher_summary("mlb", cli.build_watcher("mlb", cfg, dry_run=True))
    assert "MLB" in summary and "dry-run" in summary


# -- the lane wrapper ------------------------------------------------------
class Boom:
    """A watcher whose loop raises, as an unguarded ``process`` could."""

    def run(self, once=False):
        raise RuntimeError("upstream changed shape")


def test_lane_survives_a_crashing_watcher_and_says_so(caplog):
    """One league dying must not take the process -- but must not be silent."""
    failed = set()
    cli._lane("npb", Boom(), failed=failed)
    assert "NPB" in caplog.text
    assert failed == {"npb"}


# -- cmd_all ---------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.calls = []

    def run(self, once=False):
        self.calls.append(once)


def install(monkeypatch, built):
    monkeypatch.setattr(cli, "build_watcher",
                        lambda league, cfg, **kw: built.setdefault(league, Recorder()))
    monkeypatch.setattr(cli, "watcher_summary", lambda league, watcher: league)


def test_all_runs_every_league_once(monkeypatch, cfg):
    built = {}
    install(monkeypatch, built)
    assert cli.cmd_all(all_args(), cfg) == 0
    assert sorted(built) == sorted(LEAGUES)
    assert all(w.calls == [True] for w in built.values())


def test_all_reports_a_crashed_lane_in_its_exit_code(monkeypatch, cfg):
    """Exiting 0 after a lane died would read as a clean shutdown."""
    monkeypatch.setattr(cli, "build_watcher", lambda league, cfg, **kw: Boom())
    monkeypatch.setattr(cli, "watcher_summary", lambda league, watcher: league)
    assert cli.cmd_all(all_args(), cfg) == 1


def test_all_once_stops_cleanly_on_ctrl_c(monkeypatch, cfg):
    class Interrupted:
        def run(self, once=False):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "build_watcher", lambda league, cfg, **kw: Interrupted())
    monkeypatch.setattr(cli, "watcher_summary", lambda league, watcher: league)
    assert cli.cmd_all(all_args(), cfg) == 0


def test_all_narrows_to_the_named_leagues(monkeypatch, cfg):
    built = {}
    install(monkeypatch, built)
    cli.cmd_all(all_args(league=["npb"]), cfg)
    assert list(built) == ["npb"]


def test_all_starts_one_lane_per_league_not_per_flag(monkeypatch, cfg):
    """``--league npb --league npb`` is one NPB watcher, not two."""
    seen = []
    monkeypatch.setattr(cli, "build_watcher",
                        lambda league, cfg, **kw: seen.append(league) or Recorder())
    monkeypatch.setattr(cli, "watcher_summary", lambda league, watcher: league)
    cli.cmd_all(all_args(league=["npb", "cpbl", "npb"]), cfg)
    assert seen == ["npb", "cpbl"]


def test_parser_routes_all_to_cmd_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "cmd_all", lambda args, cfg: seen.update(vars(args)) or 0)
    monkeypatch.setattr(cli.config_mod, "load", lambda path: dict(DEFAULTS))
    assert cli.main(["all", "--dry-run", "--once", "--league", "mlb"]) == 0
    assert (seen["league"], seen["once"], seen["dry_run"]) == (["mlb"], True, True)


def test_parser_rejects_a_league_that_is_not_one(capsys):
    with pytest.raises(SystemExit):
        cli.main(["all", "--league", "kbo"])
    assert "kbo" in capsys.readouterr().err
