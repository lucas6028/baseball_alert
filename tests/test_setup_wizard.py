"""Interactive setup behavior without contacting notification services."""

import json

from cpbl_alert import setup_wizard


def _answers(values):
    iterator = iter(values)
    return lambda _prompt: next(iterator)


def test_setup_writes_configuration_and_can_skip_test(tmp_path):
    path = tmp_path / "config.json"
    normal = _answers([
        "-100123",  # Telegram chat
        "n",        # no per-league overrides
        "60",       # threshold
        "台鋼雄鷹, 富邦悍將",
        "673548",   # MLB extras
        "王彥程",   # NPB extras
        "12", "25", "35",
        "n",        # do not test
    ])
    secrets = _answers([
        "123456:ABC_token",  # Telegram token
        "-",                 # no Discord
    ])

    cfg, should_test = setup_wizard.configure(
        path, input_fn=normal, secret_fn=secrets, output=lambda _line: None,
    )

    assert should_test is False
    assert cfg["telegram_token"] == "123456:ABC_token"
    assert cfg["telegram_chat_id"] == "-100123"
    assert cfg["threshold"] == 60
    assert cfg["teams"] == ["台鋼雄鷹", "富邦悍將"]
    assert cfg["mlb_players"] == ["673548"]
    assert cfg["npb_players"] == ["王彥程"]
    assert json.loads(path.read_text(encoding="utf-8")) == cfg


def test_rerun_keeps_secrets_unknown_keys_and_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "telegram_token": "123456:old_secret",
        "discord_webhook": "https://discord.com/api/webhooks/1/token",
        "future_setting": True,
    }), encoding="utf-8")
    # chat id, split?, threshold, three lists, three polls, test?
    normal = _answers(["", "n", "", "", "", "", "", "", "", "n"])
    secrets = _answers(["", ""])

    cfg, _ = setup_wizard.configure(
        path, input_fn=normal, secret_fn=secrets, output=lambda _line: None,
    )

    assert cfg["telegram_token"] == "123456:old_secret"
    assert cfg["discord_webhook"].endswith("/1/token")
    assert cfg["future_setting"] is True
    assert cfg["poll_seconds"] == 15


def test_invalid_values_are_prompted_again(tmp_path):
    path = tmp_path / "config.json"
    normal = _answers([
        "bad chat", "42",
        "n",
        "101", "55",
        "", "", "",
        "9", "10",
        "10",
        "14", "15",
        "n",
    ])
    secrets = _answers([
        "bad token", "123456:good_token",
        "not a webhook", "-",
    ])
    messages = []

    cfg, _ = setup_wizard.configure(
        path, input_fn=normal, secret_fn=secrets, output=messages.append,
    )

    assert cfg["telegram_chat_id"] == "42"
    assert cfg["threshold"] == 55
    assert cfg["poll_seconds"] == 10
    assert cfg["npb_poll_seconds"] == 15
    assert any("Invalid" in message or "Enter" in message for message in messages)


def test_setup_explains_where_credentials_come_from(tmp_path):
    path = tmp_path / "config.json"
    normal = _answers(["", "n", "", "", "", "", "", "", "", "n"])
    secrets = _answers(["", ""])
    messages = []

    setup_wizard.configure(
        path, input_fn=normal, secret_fn=secrets, output=messages.append,
    )

    screen = "\n".join(messages)
    assert "https://t.me/BotFather" in screen
    assert "python -m cpbl_alert.cli chat-id" in screen
    assert "Server Settings → Integrations → Webhooks" in screen
    assert "SAVE & TEST" in screen


def test_color_can_be_enabled_without_leaking_into_saved_values(tmp_path):
    path = tmp_path / "config.json"
    normal = _answers(["", "n", "", "", "", "", "", "", "", "n"])
    secrets = _answers(["", ""])
    messages = []

    cfg, _ = setup_wizard.configure(
        path, input_fn=normal, secret_fn=secrets, output=messages.append,
        color=True,
    )

    assert any("\033[" in message for message in messages)
    assert "\033[" not in path.read_text(encoding="utf-8")
    assert cfg["telegram_token"] == ""
