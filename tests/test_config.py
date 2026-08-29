"""Configuration, and which channel each league resolves to."""

import io
import json
import os

import pytest

from cpbl_alert import cli, config
from cpbl_alert.notifier import ConsoleNotifier, DiscordNotifier

WEBHOOK = "https://discord.com/api/webhooks/111/tok"
OTHER = "https://discord.com/api/webhooks/222/tok"


@pytest.fixture
def cfg_file(tmp_path):
    def write(**values):
        path = tmp_path / "config.json"
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(values, fh)
        return str(path)
    return write


ENV_PREFIXES = ("TELEGRAM_", "DISCORD_", "CPBL_", "MLB_")


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """The developer's own DISCORD_WEBHOOK must not decide a test."""
    for name in [n for n in os.environ if n.startswith(ENV_PREFIXES)]:
        monkeypatch.delenv(name, raising=False)


# -- defaults --------------------------------------------------------------
def test_every_league_has_a_channel_key():
    """A league nobody configured still has somewhere to fall back from."""
    for league in config.LEAGUES:
        assert f"discord_webhook_{league}" in config.DEFAULTS
        assert f"telegram_chat_id_{league}" in config.DEFAULTS


def test_the_example_config_only_uses_keys_that_exist(cfg_file):
    """A typo in the example ships as a setting that silently does nothing."""
    with io.open("config.example.json", encoding="utf-8") as fh:
        example = json.load(fh)
    assert set(example) <= set(config.DEFAULTS)


# -- environment overrides -------------------------------------------------
def test_environment_beats_the_file(monkeypatch, cfg_file):
    monkeypatch.setenv("DISCORD_WEBHOOK", OTHER)
    cfg = config.load(cfg_file(discord_webhook=WEBHOOK))
    assert cfg["discord_webhook"] == OTHER


def test_each_league_has_its_own_environment_variable(monkeypatch, cfg_file):
    monkeypatch.setenv("DISCORD_WEBHOOK_MLB", OTHER)
    monkeypatch.setenv("TELEGRAM_CHAT_ID_CPBL", "42")
    cfg = config.load(cfg_file(discord_webhook=WEBHOOK))
    assert cfg["discord_webhook_mlb"] == OTHER
    assert cfg["discord_webhook_cpbl"] == ""      # untouched, so it shares
    assert cfg["telegram_chat_id_cpbl"] == "42"


def test_a_missing_config_file_is_just_defaults(tmp_path):
    assert config.load(str(tmp_path / "nope.json")) == config.DEFAULTS


# -- what `test` sends to ---------------------------------------------------
def test_one_shared_channel_is_tested_once():
    """Two leagues in one channel is one message, listing both."""
    targets = cli.channels_to_test({"discord_webhook": WEBHOOK})
    assert len(targets) == 1
    leagues, notifier = targets[0]
    assert leagues == list(config.LEAGUES)
    assert isinstance(notifier, DiscordNotifier)


def test_split_channels_are_tested_separately():
    targets = cli.channels_to_test({"discord_webhook_cpbl": WEBHOOK,
                                "discord_webhook_mlb": OTHER})
    assert [leagues for leagues, _ in targets] == [["cpbl"], ["mlb"]]
    assert [n.webhook_url for _, n in targets] == [WEBHOOK, OTHER]


def test_one_league_can_be_tested_alone():
    targets = cli.channels_to_test({"discord_webhook_mlb": OTHER}, "mlb")
    assert [leagues for leagues, _ in targets] == [["mlb"]]
    assert targets[0][1].webhook_url == OTHER


def test_an_unconfigured_league_still_shows_up():
    """Nothing configured for CPBL is a finding, not a reason to say nothing."""
    by_league = {leagues[0]: n
                 for leagues, n in cli.channels_to_test({"discord_webhook_mlb": OTHER})}
    assert isinstance(by_league["cpbl"], ConsoleNotifier)
    assert isinstance(by_league["mlb"], DiscordNotifier)


def test_the_test_message_names_the_league_it_is_testing():
    """With the leagues split, what matters is that the *right* one arrives."""
    assert "中職" in cli.setup_message(["cpbl"])
    assert "大聯盟" not in cli.setup_message(["cpbl"])
    assert "大聯盟" in cli.setup_message(["mlb"])
    both = cli.setup_message(list(config.LEAGUES))
    assert "中職" in both and "大聯盟" in both
