"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import requests

from . import config as config_mod
from .client import CpblClient, CpblError
from .dedupe import GameTracker
from .leverage import assess
from .models import state_from_row
from .notifier import build_notifier, format_alert
from .watcher import Watcher, today_tw

STATUS_LABELS = {0: "?", 1: "pending", 2: "LIVE", 3: "final",
                 4: "lineup", 5: "postponed", 6: "cancelled", 7: "suspended",
                 8: "delayed"}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_live(args, cfg) -> int:
    """List today's games with their real status."""
    client = CpblClient()
    day = args.date or today_tw()
    games = client.games_on(day)
    if not games:
        print(f"no games scheduled on {day}")
        return 0
    print(f"games on {day}:")
    for g in games:
        sno = g.get("GameSno")
        status = 0
        try:
            # Schedule GameStatus is always null; the truth is in getlive.
            status = client.game_live(sno, str(g.get("Year") or day[:4]),
                                      str(g.get("KindCode") or "A"))["status"]
        except (CpblError, requests.RequestException) as exc:
            logging.debug("status lookup failed for %s: %s", sno, exc)
        label = STATUS_LABELS.get(status, str(status))
        start = str(g.get("GameDateTimeS") or "")[11:16]
        print(f"  #{sno} [{label:<8}] {start}  "
              f"{g.get('VisitingTeamName', '?')} {g.get('VisitingScore', '')} - "
              f"{g.get('HomeScore', '')} {g.get('HomeTeamName', '?')}")
    return 0


def cmd_check(args, cfg) -> int:
    """Replay one real game through the model and show what would fire.

    Useful for sanity-checking a threshold against last night's game.
    """
    client = CpblClient()
    payload = client.game_live(args.game_sno, args.year, args.kind)
    meta, rows = payload["meta"], payload["rows"]
    print(f"{meta.get('VisitingTeamName', '?')} {meta.get('VisitingScore')} - "
          f"{meta.get('HomeScore')} {meta.get('HomeTeamName', '?')}  "
          f"[{STATUS_LABELS.get(payload['status'], payload['status'])}]  "
          f"{len(rows)} pitches\n")

    threshold = float(args.threshold if args.threshold is not None else cfg["threshold"])
    tracker = GameTracker()
    fired = 0
    for row in rows:
        st = state_from_row(row, meta)
        a = assess(st, threshold=threshold)
        hit = tracker.should_fire(st, a)
        if hit:
            fired += 1
        if hit or args.all:
            mark = "FIRE " if hit else f"{a.tier:<5}"
            print(f"  [{a.tension:5.1f}] {mark} {st.describe():<32} "
                  f"{st.batter[:5]:<6} | {' / '.join(a.reasons)}")
    print(f"\nwould have sent {fired} notification(s) at threshold {threshold}")
    return 0


def cmd_chat_id(args, cfg) -> int:
    token = cfg.get("telegram_token")
    if not token:
        print("set telegram_token in config.json (or the TELEGRAM_TOKEN env var) first")
        return 1
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
    data = resp.json()
    if not data.get("ok"):
        print("telegram error:", data)
        return 1
    seen = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            seen[chat["id"]] = (chat.get("title") or chat.get("username")
                                or chat.get("first_name", ""))
    if not seen:
        print("no messages yet -- send your bot any message, then run this again")
        return 1
    for cid, name in seen.items():
        print(f"chat_id={cid}  ({name})")
    return 0


def cmd_test(args, cfg) -> int:
    notifier = build_notifier(cfg)
    ok = notifier.send(
        "<b>⚾ CPBL chance alert</b>\n設定成功，這是測試訊息。\n"
        "This is roughly what a chance alert will look like."
    )
    print("sent" if ok else "failed")
    return 0 if ok else 1


def cmd_run(args, cfg) -> int:
    watcher = Watcher(
        client=CpblClient(),
        notifier=build_notifier(cfg),
        threshold=float(args.threshold if args.threshold is not None else cfg["threshold"]),
        poll_seconds=int(args.poll or cfg["poll_seconds"]),
        teams=cfg.get("teams"),
        dry_run=args.dry_run,
    )
    print(f"watching CPBL (threshold {watcher.threshold}, poll {watcher.poll_seconds}s"
          + (f", teams {watcher.teams}" if watcher.teams else "") + ")")
    try:
        watcher.run(once=args.once)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cpbl-alert",
        description="快轉台 -- 中職關鍵時刻通知 (alert when a CPBL game gets tense)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="watch live games and send alerts")
    r.add_argument("--threshold", type=float, default=None)
    r.add_argument("--poll", type=int, default=None)
    r.add_argument("--once", action="store_true", help="single pass, then exit")
    r.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    r.set_defaults(func=cmd_run)

    lv = sub.add_parser("live", help="list today's games and their status")
    lv.add_argument("--date", default=None, help="YYYY-MM-DD (default: today in Taiwan)")
    lv.set_defaults(func=cmd_live)

    ck = sub.add_parser("check", help="replay a real game through the model")
    ck.add_argument("game_sno")
    ck.add_argument("--year", default=None)
    ck.add_argument("--kind", default="A")
    ck.add_argument("--threshold", type=float, default=None)
    ck.add_argument("--all", action="store_true", help="show every pitch, not just alerts")
    ck.set_defaults(func=cmd_check)

    c = sub.add_parser("chat-id", help="discover your Telegram chat id")
    c.set_defaults(func=cmd_chat_id)

    t = sub.add_parser("test", help="send a test notification")
    t.set_defaults(func=cmd_test)

    args = p.parse_args(argv)
    if getattr(args, "year", None) is None and args.cmd == "check":
        args.year = today_tw()[:4]
    _setup_logging(args.verbose)
    cfg = config_mod.load(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
