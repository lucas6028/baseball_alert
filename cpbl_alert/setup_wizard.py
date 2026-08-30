"""Interactive configuration wizard for first-time and repeat setup."""

from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from urllib.parse import urlparse

from .config import CONFIG_PATH, DEFAULTS, LEAGUES
from .terminal import (
    BOLD, CYAN, DIM, GREEN, MAGENTA, RED, UNDERLINE, YELLOW,
    paint, supports_color,
)

Input = Callable[[str], str]
Output = Callable[[str], None]

_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")
_CHAT_RE = re.compile(r"^(?:-?\d+|@[A-Za-z0-9_]+)$")

_RULE = "─" * 64


def _section(output: Output, number: int, title: str, color: bool) -> None:
    output(paint(f"\n{_RULE}", CYAN, enabled=color))
    output(paint(f"  {number}/3  {title}", BOLD, CYAN, enabled=color))
    output(paint(_RULE, CYAN, enabled=color))


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"cannot read {path}: the top-level JSON value must be an object")
    return value


def _write(path: Path, cfg: dict) -> None:
    """Replace the config atomically so an interrupted setup cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            temporary = Path(fh.name)
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _ask(
    label: str,
    current,
    *,
    input_fn: Input,
    output: Output,
    secret_fn: Input | None = None,
    parse: Callable[[str], object] = str,
    validate: Callable[[object], str | None] | None = None,
    allow_clear: bool = True,
    color: bool = False,
):
    secret = secret_fn is not None
    if secret:
        state = "set ✓" if current else "not set"
        action = "; Enter keeps it, - clears it" if allow_clear else "; Enter keeps it"
        prompt = (paint("  › ", CYAN, enabled=color) +
                  paint(label, BOLD, enabled=color) +
                  paint(f" [{state}{action}]: ", DIM, enabled=color))
    else:
        shown = ", ".join(str(v) for v in current) if isinstance(current, list) else str(current)
        shown = shown or "not set"
        action = "; Enter keeps it, - clears it" if allow_clear else "; Enter keeps it"
        prompt = (paint("  › ", CYAN, enabled=color) +
                  paint(label, BOLD, enabled=color) +
                  paint(f" [{shown}{action}]: ", DIM, enabled=color))

    while True:
        raw = (secret_fn or input_fn)(prompt).strip()
        if not raw:
            return current
        if raw == "-":
            if allow_clear:
                return [] if isinstance(current, list) else ""
            output(paint("    ! This value cannot be cleared; please enter a value or press Enter.",
                         YELLOW, enabled=color))
            continue
        try:
            value = parse(raw)
        except (TypeError, ValueError):
            output(paint("    ! Invalid value; please try again.", RED, enabled=color))
            continue
        error = validate(value) if validate else None
        if error:
            output(paint(f"    ! {error}", RED, enabled=color))
            continue
        return value


def _confirm(label: str, default: bool, input_fn: Input, output: Output,
             color: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        prompt = (paint("  › ", CYAN, enabled=color) +
                  paint(label, BOLD, enabled=color) +
                  paint(f" [{suffix}]: ", DIM, enabled=color))
        answer = input_fn(prompt).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        output(paint("    ! Please enter y or n.", RED, enabled=color))


def _token_error(value: object) -> str | None:
    return None if _TOKEN_RE.fullmatch(str(value)) else "A Telegram bot token looks like 123456:ABC..."


def _chat_error(value: object) -> str | None:
    return None if _CHAT_RE.fullmatch(str(value)) else "Use a numeric chat id (it may start with -) or @channel_name."


def _webhook_error(value: object) -> str | None:
    parsed = urlparse(str(value))
    host = (parsed.hostname or "").lower()
    allowed = host == "discord.com" or host == "discordapp.com" or host.endswith(".discord.com")
    if parsed.scheme == "https" and allowed and re.match(r"^/api(?:/v\d+)?/webhooks/[^/]+/[^/]+", parsed.path):
        return None
    return "Paste the complete HTTPS Discord webhook URL."


def _number(minimum: float, maximum: float | None = None):
    def validate(value: object) -> str | None:
        number = float(value)
        if number < minimum or (maximum is not None and number > maximum):
            if maximum is None:
                return f"Enter a number of at least {minimum:g}."
            return f"Enter a number between {minimum:g} and {maximum:g}."
        return None
    return validate


def _csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def configure(
    path: str | os.PathLike[str] = CONFIG_PATH,
    *,
    input_fn: Input = input,
    secret_fn: Input = getpass.getpass,
    output: Output = print,
    color: bool | None = None,
) -> tuple[dict, bool]:
    """Prompt for settings, save them, and return (config, test_requested)."""
    destination = Path(path)
    existing = _read(destination)
    cfg = dict(DEFAULTS)
    cfg.update(existing)  # Keep settings added by newer versions or by the user.

    use_color = (supports_color() if output is print else False) if color is None else color

    def ask(label, current, **kwargs):
        return _ask(label, current, input_fn=input_fn, output=output,
                    color=use_color, **kwargs)

    def confirm(label, default):
        return _confirm(label, default, input_fn, output, color=use_color)

    output(paint("╭─ ⚾ 快轉台 setup", BOLD, MAGENTA, enabled=use_color))
    output(paint("│  Configure alerts and verify they reach your phone.", MAGENTA,
                 enabled=use_color))
    output(paint("╰──────────────────────────────────────────────────────────────",
                 MAGENTA, enabled=use_color))
    output(f"  {paint('Config', BOLD, enabled=use_color)}  {destination.resolve()}")
    output(paint("  Enter   keep current value    -   clear optional value", DIM,
                 enabled=use_color))
    output(paint("  Privacy tokens and webhook URLs are hidden while you type", DIM,
                 enabled=use_color))

    _section(output, 1, "NOTIFICATION DESTINATIONS", use_color)
    output(paint("\n  TELEGRAM", BOLD, CYAN, enabled=use_color))
    botfather = paint("https://t.me/BotFather", UNDERLINE, CYAN, enabled=use_color)
    output(f"  Bot token  Open {botfather}, send /newbot, then copy")
    output("             the token BotFather gives you.")
    output("  Chat ID    Send your new bot a message. If you do not know the ID,")
    output("             leave it blank, finish setup, then run:")
    output("             uv run python -m cpbl_alert.cli chat-id")
    output("             Rerun init.py and enter the reported chat_id value.\n")
    cfg["telegram_token"] = ask(
        "Telegram bot token", cfg["telegram_token"],
        secret_fn=secret_fn, validate=_token_error,
    )
    cfg["telegram_chat_id"] = ask(
        "Telegram chat id", cfg["telegram_chat_id"], validate=_chat_error,
    )
    output(paint("\n  DISCORD", BOLD, MAGENTA, enabled=use_color))
    output("  Webhook    Server Settings → Integrations → Webhooks → New Webhook")
    output("             Choose a channel, then select Copy Webhook URL.\n")
    cfg["discord_webhook"] = ask(
        "Shared Discord webhook", cfg["discord_webhook"],
        secret_fn=secret_fn, validate=_webhook_error,
    )

    split_existing = any(
        cfg.get(f"{kind}_{league}")
        for league in LEAGUES
        for kind in ("telegram_chat_id", "discord_webhook")
    )
    output("")
    if confirm("Use separate destinations for CPBL / MLB / NPB?", split_existing):
        labels = {"cpbl": "CPBL", "mlb": "MLB", "npb": "NPB"}
        for league in LEAGUES:
            cfg[f"telegram_chat_id_{league}"] = ask(
                f"{labels[league]} Telegram chat override",
                cfg[f"telegram_chat_id_{league}"], validate=_chat_error,
            )
            cfg[f"discord_webhook_{league}"] = ask(
                f"{labels[league]} Discord webhook override",
                cfg[f"discord_webhook_{league}"], secret_fn=secret_fn,
                validate=_webhook_error,
            )

    _section(output, 2, "ALERT PREFERENCES", use_color)
    output(paint("  Keep the defaults if you are unsure; you can rerun this anytime.\n",
                 DIM, enabled=use_color))
    cfg["threshold"] = ask(
        "CPBL Leverage Index threshold (1.0 = average)", cfg["threshold"],
        parse=float, validate=_number(0, 30),
        allow_clear=False,
    )
    if isinstance(cfg["threshold"], float) and cfg["threshold"].is_integer():
        cfg["threshold"] = int(cfg["threshold"])
    cfg["teams"] = ask(
        "CPBL teams, comma-separated (empty means all)", cfg["teams"],
        parse=_csv,
    )
    cfg["mlb_players"] = ask(
        "Extra MLB player ids/names, comma-separated", cfg["mlb_players"],
        parse=_csv,
    )
    cfg["npb_players"] = ask(
        "Extra NPB player names, comma-separated", cfg["npb_players"],
        parse=_csv,
    )
    for key, label, minimum in (
        ("poll_seconds", "CPBL poll seconds", 10),
        ("mlb_poll_seconds", "MLB poll seconds", 10),
        ("npb_poll_seconds", "NPB poll seconds", 15),
    ):
        cfg[key] = ask(label, cfg[key], parse=int, validate=_number(minimum),
                       allow_clear=False)

    _section(output, 3, "SAVE & TEST", use_color)
    _write(destination, cfg)
    output(paint("  ✓ Configuration saved", BOLD, GREEN, enabled=use_color))
    output(paint(f"    {destination.resolve()}", DIM, enabled=use_color))
    telegram_chat = (cfg.get("telegram_chat_id") or
                     any(cfg.get(f"telegram_chat_id_{lg}") for lg in LEAGUES))
    has_remote = bool(cfg.get("discord_webhook") or
                      any(cfg.get(f"discord_webhook_{lg}") for lg in LEAGUES) or
                      (cfg.get("telegram_token") and telegram_chat))
    if not has_remote:
        output(paint("\n  ! No complete remote destination is configured.", YELLOW,
                     enabled=use_color))
        output(paint("    The test will print to this console instead.", YELLOW,
                     enabled=use_color))
    output("")
    should_test = confirm("Send test notification(s) now?", True)
    return cfg, should_test


def run(path: str | os.PathLike[str] = CONFIG_PATH) -> int:
    use_color = supports_color()
    try:
        cfg, should_test = configure(path)
    except (EOFError, KeyboardInterrupt):
        print(paint("\n  ! Setup cancelled.", YELLOW, enabled=use_color))
        return 130
    except (OSError, ValueError) as exc:
        print(paint(f"\n  ✗ Setup failed: {exc}", BOLD, RED, enabled=use_color))
        return 1
    if not should_test:
        print(paint("\n  ℹ Saved without testing.", CYAN, enabled=use_color))
        print(paint("    Run `uv run python -m cpbl_alert.cli test` whenever you are ready.",
                    DIM, enabled=use_color))
        return 0

    # Import lazily so this module can also back the CLI's `init` command.
    from .cli import cmd_test
    print(paint("\n  Sending test notification(s)…\n", BOLD, MAGENTA,
                enabled=use_color))
    return cmd_test(SimpleNamespace(league=None, ruler=False), cfg)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Configure 快轉台 and test notifications")
    parser.add_argument("--config", default=CONFIG_PATH, help="config file to create or update")
    return run(parser.parse_args(argv).config)


if __name__ == "__main__":
    raise SystemExit(main())
