"""Configuration: file first, environment overrides on top."""

from __future__ import annotations

import io
import json
import os

DEFAULTS = {
    "telegram_token": "",
    "telegram_chat_id": "",
    "threshold": 55.0,
    "poll_seconds": 15,
    "teams": [],          # e.g. ["中信兄弟"] -- empty means all games
    # MLB. Nationality comes from the API (birthCountry), so there is no list
    # to keep here; this one is the escape hatch for a player MLB does not
    # record as Taiwan-born, given as ids or as full names.
    "mlb_players": [],
    "mlb_poll_seconds": 20,
}

CONFIG_PATH = os.environ.get("CPBL_ALERT_CONFIG", "config.json")


def load(path: str | None = None) -> dict:
    cfg = dict(DEFAULTS)
    path = path or CONFIG_PATH
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    # Environment wins -- convenient for servers and keeps tokens out of files.
    if os.environ.get("TELEGRAM_TOKEN"):
        cfg["telegram_token"] = os.environ["TELEGRAM_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram_chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if os.environ.get("CPBL_THRESHOLD"):
        cfg["threshold"] = float(os.environ["CPBL_THRESHOLD"])
    if os.environ.get("CPBL_TEAMS"):
        cfg["teams"] = [t.strip() for t in os.environ["CPBL_TEAMS"].split(",") if t.strip()]
    if os.environ.get("MLB_PLAYERS"):
        cfg["mlb_players"] = [p.strip() for p in os.environ["MLB_PLAYERS"].split(",")
                              if p.strip()]
    return cfg
