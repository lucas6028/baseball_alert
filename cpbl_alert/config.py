"""Configuration: file first, environment overrides on top."""

from __future__ import annotations

import io
import json
import logging
import os

log = logging.getLogger(__name__)

# The leagues this thing watches, which are also the three things it can route
# separately. A league name is a config-key suffix (``discord_webhook_npb``)
# and an environment-variable suffix (``DISCORD_WEBHOOK_NPB``), so a fourth
# league needs its name here, its two channel keys in DEFAULTS, and nothing
# else -- the routing, the env overrides and ``test`` all read this tuple.
LEAGUES = ("cpbl", "mlb", "npb")

DEFAULTS = {
    # -- where alerts land -------------------------------------------------
    # Telegram and Discord are independent: configure either, or both, and
    # both get the alert. A league-suffixed key overrides the plain one for
    # that league alone, which is how 中職, 大聯盟 and 日職 end up in three
    # different channels while still sharing one bot / one webhook by default.
    "telegram_token": "",
    "telegram_chat_id": "",
    "telegram_chat_id_cpbl": "",
    "telegram_chat_id_mlb": "",
    "telegram_chat_id_npb": "",
    # A Discord webhook URL *is* a channel -- that is the whole mechanism.
    "discord_webhook": "",
    "discord_webhook_cpbl": "",
    "discord_webhook_mlb": "",
    "discord_webhook_npb": "",
    # -- what fires --------------------------------------------------------
    "threshold": 2.0,
    "poll_seconds": 15,
    "teams": [],          # e.g. ["中信兄弟"] -- empty means all games
    # MLB. Nationality comes from the API (birthCountry), so there is no list
    # to keep here; this one is the escape hatch for a player MLB does not
    # record as Taiwan-born, given as ids or as full names.
    "mlb_players": [],
    "mlb_poll_seconds": 20,
    # NPB. There is no nationality field on npb.jp, so the built-in table is
    # the whole detector rather than a backstop -- this is where you add a
    # player it does not know yet, by the name the site prints (either
    # orthography; they are matched folded).
    "npb_players": [],
    "npb_poll_seconds": 30,
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
    if os.environ.get("DISCORD_WEBHOOK"):
        cfg["discord_webhook"] = os.environ["DISCORD_WEBHOOK"]
    # The per-league overrides, which are the same two settings with a league
    # on the end. Spelling them out one by one would be six near-identical
    # blocks that a fourth league would turn into eight.
    for league in LEAGUES:
        for key in ("telegram_chat_id", "discord_webhook"):
            env = f"{key}_{league}".upper()
            if os.environ.get(env):
                cfg[f"{key}_{league}"] = os.environ[env]
    if os.environ.get("CPBL_THRESHOLD"):
        cfg["threshold"] = float(os.environ["CPBL_THRESHOLD"])
    if os.environ.get("CPBL_TEAMS"):
        cfg["teams"] = [t.strip() for t in os.environ["CPBL_TEAMS"].split(",") if t.strip()]
    if os.environ.get("MLB_PLAYERS"):
        cfg["mlb_players"] = [p.strip() for p in os.environ["MLB_PLAYERS"].split(",")
                              if p.strip()]
    if os.environ.get("NPB_PLAYERS"):
        cfg["npb_players"] = [p.strip() for p in os.environ["NPB_PLAYERS"].split(",")
                              if p.strip()]
    # Versions before LI used a 0-100 heartbeat threshold (55 by default).
    # Such a value would silence the unbounded-but-normally-single-digit LI
    # scale, so migrate it rather than making an existing installation inert.
    try:
        if float(cfg["threshold"]) > 30:
            log.warning("migrating legacy heartbeat threshold %s to LI %.1f",
                        cfg["threshold"], DEFAULTS["threshold"])
            cfg["threshold"] = DEFAULTS["threshold"]
    except (TypeError, ValueError):
        pass
    return cfg
