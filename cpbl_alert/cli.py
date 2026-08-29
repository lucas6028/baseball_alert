"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

import requests

from . import config as config_mod
from . import mlb
from . import npb
from .client import CpblClient, CpblError
from .config import LEAGUES
from .dedupe import GameTracker
from .leverage import assess
from .models import state_from_row
from .notifier import (
    RULER_LINES,
    RULER_WIDTH,
    Notifier,
    build_notifier,
    format_alert,
    ruler_text,
)
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
    """Send a test message to every channel that is configured.

    Every league by default, because the point of a test is to find the
    channel you got wrong, and a per-league setup has three of them to get
    wrong. Leagues pointed at one channel share one message rather than
    getting one each -- the channel is what is being tested, not the league.
    """
    ok = True
    for leagues, notifier in channels_to_test(cfg, args.league):
        sent = (_send_ruler(notifier) if args.ruler
                else notifier.send(setup_message(leagues)))
        print(f"{'/'.join(leagues)}: {notifier.label} -- "
              f"{'sent' if sent else 'failed'}")
        ok = ok and sent
    if args.ruler and ok:
        print(RULER_HELP)
    return 0 if ok else 1


# What a channel is for, in the words the alerts themselves use.
LEAGUE_LABELS = {"cpbl": "中職", "mlb": "大聯盟", "npb": "日職"}


def setup_message(leagues) -> str:
    """The test alert, naming the leagues that will arrive in this channel.

    Which is the thing worth checking once the two are split up: not that
    something arrives, but that the right one does. No 快轉台 in the body --
    the bot's display name titles the Telegram notification and the
    webhook's name sits above the Discord one.
    """
    named = "、".join(LEAGUE_LABELS.get(lg, lg) for lg in leagues)
    return ("測試 <b>0-0</b> 測試\n"
            f"設定完成　{named}的通知會像這樣推給你")


def channels_to_test(cfg, league: str | None = None) -> list[tuple[list[str], Notifier]]:
    """The distinct channels to test, each with the leagues that use it."""
    targets: list[tuple[list[str], Notifier]] = []
    seen: dict[tuple, int] = {}
    for name in ([league] if league else list(LEAGUES)):
        notifier = build_notifier(cfg, name)
        if notifier.key in seen:
            targets[seen[notifier.key]][0].append(name)
            continue
        seen[notifier.key] = len(targets)
        targets.append(([name], notifier))
    return targets


RULER_HELP = (
    f"ruler sent -- {RULER_LINES} numbered lines.\n"
    "Look at your lock screen WITHOUT expanding the notification:\n"
    "  1. the last number you can still read is your line budget\n"
    f"  2. every ┤ should sit on the same row as its number; if one "
    f"wrapped, {RULER_WIDTH} columns is too wide\n"
    "\nnote: the title of a Telegram notification is the chat name, so "
    "whatever you named the bot is already on screen above line 1.")


def _send_ruler(notifier) -> bool:
    """Push a ruler so the alert can be sized against a real phone.

    Nobody can tell you how many lines your notifications show: it moves with
    the OS, the launcher and the font-size setting. So look at yours.
    """
    return notifier.send(ruler_text())


def _destination(watcher) -> str:
    """Where this watcher's alerts will land, for the line it prints on start.

    Worth printing at all because the channel is now a per-league setting:
    the way you find out you pointed both leagues at the same webhook is
    reading it here, not by waiting for a rally.
    """
    return "dry-run" if watcher.dry_run else watcher.notifier.label


def cmd_mlb(args, cfg) -> int:
    """Watch MLB and push when a Taiwanese player is on stage."""
    watcher = mlb.TaiwaneseWatcher(
        client=mlb.MlbClient(),
        notifier=build_notifier(cfg, "mlb"),
        extra_players=cfg.get("mlb_players"),
        poll_seconds=int(args.poll or cfg.get("mlb_poll_seconds") or 20),
        dry_run=args.dry_run,
    )
    print(f"watching MLB for Taiwanese players (poll {watcher.poll_seconds}s)"
          f" -> {_destination(watcher)}")
    try:
        watcher.run(once=args.once)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_mlb_live(args, cfg) -> int:
    """List MLB games in the current window, marking who is on stage.

    The window is two US business dates wide -- a night game in the States is
    the next morning in Taiwan -- so both of them are listed.
    """
    client = mlb.MlbClient()
    start, end = mlb.window()
    games = client.schedule(start, end)
    if not games:
        print(f"no MLB games between {start} and {end}")
        return 0
    watcher = mlb.TaiwaneseWatcher(client, build_notifier(cfg, "mlb"),
                                   extra_players=cfg.get("mlb_players"))
    try:
        watcher.roster = client.taiwanese_players(end[:4], end)
    except (mlb.MlbError, requests.RequestException) as exc:
        logging.debug("roster lookup failed: %s", exc)

    print(f"MLB games {start} .. {end}:")
    for game in games:
        state = (game.get("status") or {}).get("abstractGameState", "?")
        line = game.get("linescore") or {}
        away, home = ((game.get("teams") or {}).get("away") or {},
                      (game.get("teams") or {}).get("home") or {})
        situation = ""
        if state == "Live":
            batter = (line.get("offense") or {}).get("batter") or {}
            pitcher = (line.get("defense") or {}).get("pitcher") or {}
            flag = " <-- 台灣選手" if (watcher.is_taiwanese(batter)
                                        or watcher.is_taiwanese(pitcher)) else ""
            situation = (f"  {line.get('currentInning', '?')}"
                         f"{'top' if line.get('isTopInning') else 'bot'} "
                         f"{line.get('outs', 0)}out  "
                         f"B:{mlb.display_name(batter)} P:{mlb.display_name(pitcher)}"
                         f"{flag}")
        print(f"  #{game.get('gamePk')} [{state:<7}] "
              f"{mlb.team_name(away)} {away.get('score', '')} - "
              f"{home.get('score', '')} {mlb.team_name(home)}{situation}")
    return 0


def cmd_mlb_players(args, cfg) -> int:
    """Who this thing would fire for, and where each name came from."""
    client = mlb.MlbClient()
    season = args.season or mlb.window()[1][:4]
    try:
        roster = client.taiwanese_players(season)
    except (mlb.MlbError, requests.RequestException) as exc:
        print(f"roster lookup failed: {exc}")
        roster = {}
    print(f"MLB {season}: {len(roster)} player(s) with birthCountry={mlb.TAIWAN}")
    for pid, name in sorted(roster.items(), key=lambda kv: kv[1]):
        zh = mlb.TAIWANESE_IDS.get(pid) or mlb.TAIWANESE_NAMES.get(name) or ""
        print(f"  {pid:<8} {name:<24} {zh}")
    extra = [p for p in (cfg.get("mlb_players") or [])]
    if extra:
        print(f"\nalso alerting on (from config): {', '.join(str(p) for p in extra)}")
    print(f"\n{len(mlb.TAIWANESE_NAMES)} name(s) known offline, used as a backstop "
          "when the roster lookup has not caught up with a call-up")
    return 0


def cmd_npb(args, cfg) -> int:
    """Watch NPB and push when a Taiwanese player is on stage."""
    watcher = npb.TaiwaneseWatcher(
        feed=npb.NpbClient(),
        notifier=build_notifier(cfg, "npb"),
        extra_players=cfg.get("npb_players"),
        poll_seconds=int(args.poll or cfg.get("npb_poll_seconds")
                         or npb.DEFAULT_POLL_SECONDS),
        dry_run=args.dry_run,
    )
    print(f"watching NPB for Taiwanese players (poll {watcher.poll_seconds}s)"
          f" -> {_destination(watcher)}")
    try:
        watcher.run(once=args.once)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_npb_live(args, cfg) -> int:
    """List today's NPB games, marking who is on stage."""
    client = npb.NpbClient()
    day = args.date or npb.today_jst()
    try:
        games = client.games(day)
    except (npb.NpbError, requests.RequestException) as exc:
        print(f"scoreboard lookup failed: {exc}")
        return 1
    if not games:
        print(f"no NPB games on {day} (JST)")
        return 0
    watcher = npb.TaiwaneseWatcher(client, build_notifier(cfg, "npb"),
                                   extra_players=cfg.get("npb_players"))
    print(f"NPB games on {day} (JST):")
    for g in games:
        situation = ""
        if g.status == "live":
            flag = " <-- 台灣選手" if (watcher.is_taiwanese(g.batter)
                                        or watcher.is_taiwanese(g.pitcher)) else ""
            situation = (f"  {g.inning}{'top' if g.is_top else 'bot'} "
                         f"{g.outs}out  B:{watcher.display(g.batter)} "
                         f"P:{watcher.display(g.pitcher)}{flag}")
        print(f"  {g.game_id} [{g.status:<7}] "
              f"{g.away_team} {g.away_score} - {g.home_score} {g.home_team}"
              f"{situation}")
    return 0


def cmd_npb_players(args, cfg) -> int:
    """Who this thing would fire for in NPB, and how each name is written."""
    print(f"NPB: {len(npb.TAIWANESE_NPB)} Taiwanese player(s) known offline")
    for written, chinese in sorted(npb.TAIWANESE_NPB.items(),
                                   key=lambda kv: kv[1]):
        variant = "" if written == chinese else f"  (npb.jp writes {written})"
        print(f"  {chinese}{variant}")
    extra = [str(p) for p in (cfg.get("npb_players") or []) if p]
    if extra:
        print(f"\nalso alerting on (from config): {', '.join(extra)}")
    print("\nnpb.jp publishes no nationality, so this table IS the detector -- "
          "a player missing from it is a player this stays silent about.\n"
          "Add one with npb_players in config.json (or the NPB_PLAYERS env var).")
    return 0


def cmd_npb_probe(args, cfg) -> int:
    """Show what the page rules matched on a real NPB page, rule by rule.

    The parsing in ``npb.py`` is anchored on the page's Japanese labels rather
    than its markup, which makes it durable but not self-verifying. This is
    how you verify it: point it at a live game, read what each rule found, and
    fix ``FIELD_PATTERNS`` if a line comes back empty that should not be.
    """
    client = npb.NpbClient()
    day = args.date or npb.today_jst()
    try:
        if args.game:
            game_ids = [args.game if "/" in args.game
                        else f"{day[:4]}/{day[5:7]}{day[8:10]}/{args.game}"]
        else:
            game_ids = client.game_ids(day)
            print(f"scoreboard {day} -> {len(game_ids)} game link(s): "
                  f"{', '.join(game_ids) or '(none)'}\n")
    except requests.RequestException as exc:
        print(f"fetch failed: {exc}")
        return 1

    for game_id in game_ids:
        try:
            page = client.game_page(game_id)
        except requests.RequestException as exc:
            print(f"{game_id}: fetch failed: {exc}")
            continue
        text = npb.strip_tags(page)
        game = npb.parse_game_page(page, game_id)
        print(f"== {game_id} ==  ({len(page)} bytes of HTML)")
        print(f"  slug teams  {game.away_team or '?'} (away) vs "
              f"{game.home_team or '?'} (home)")
        print(f"  status      {game.status}")
        for name in ("inning", "outs", "balls", "strikes", "batter",
                     "pitcher", "batting_order", "average", "pitch_count"):
            hit = npb.match_rule(text, name)
            print(f"  {name:<12}{hit.group(0).strip() if hit else '-- no rule matched'}")
        print(f"  bases       {game.first}/{game.second}/{game.third}")
        print(f"  score       {game.away_score}-{game.home_score}")
        state = npb.state_from_npb_game(game)
        print(f"  -> state    {state.describe() if state else '(not live)'}")
        if args.text:
            print("\n--- page text ---")
            print("\n".join(ln for ln in text.splitlines() if ln.strip()))
        print()
    return 0


def cmd_run(args, cfg) -> int:
    watcher = Watcher(
        client=CpblClient(),
        notifier=build_notifier(cfg, "cpbl"),
        threshold=float(args.threshold if args.threshold is not None else cfg["threshold"]),
        poll_seconds=int(args.poll or cfg["poll_seconds"]),
        teams=cfg.get("teams"),
        dry_run=args.dry_run,
    )
    print(f"watching CPBL (threshold {watcher.threshold}, poll {watcher.poll_seconds}s"
          + (f", teams {watcher.teams}" if watcher.teams else "") + ")"
          + f" -> {_destination(watcher)}")
    try:
        watcher.run(once=args.once)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cpbl-alert",
        description="快轉台 -- 中職關鍵時刻，以及大聯盟、日職台灣選手上場的通知")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="watch live games and send alerts")
    r.add_argument("--threshold", type=float, default=None)
    r.add_argument("--poll", type=int, default=None)
    r.add_argument("--once", action="store_true", help="single pass, then exit")
    r.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    r.set_defaults(func=cmd_run)

    m = sub.add_parser("mlb", help="watch MLB and alert when a Taiwanese player is up")
    m.add_argument("--poll", type=int, default=None)
    m.add_argument("--once", action="store_true", help="single pass, then exit")
    m.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    m.set_defaults(func=cmd_mlb)

    ml = sub.add_parser("mlb-live", help="list MLB games and who is on stage")
    ml.set_defaults(func=cmd_mlb_live)

    mp = sub.add_parser("mlb-players",
                        help="list the Taiwanese players MLB currently knows about")
    mp.add_argument("--season", default=None, help="YYYY (default: this season)")
    mp.set_defaults(func=cmd_mlb_players)

    n = sub.add_parser("npb", help="watch NPB and alert when a Taiwanese player is up")
    n.add_argument("--poll", type=int, default=None)
    n.add_argument("--once", action="store_true", help="single pass, then exit")
    n.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    n.set_defaults(func=cmd_npb)

    nl = sub.add_parser("npb-live", help="list today's NPB games and who is on stage")
    nl.add_argument("--date", default=None, help="YYYY-MM-DD (default: today in Japan)")
    nl.set_defaults(func=cmd_npb_live)

    np_ = sub.add_parser("npb-players",
                         help="list the Taiwanese players NPB alerts fire for")
    np_.set_defaults(func=cmd_npb_players)

    nb = sub.add_parser("npb-probe",
                        help="check the page rules against a real npb.jp page")
    nb.add_argument("game", nargs="?", default=None,
                    help="slug (f-l-01) or full id (2026/0829/f-l-01); "
                         "default: every game on the date")
    nb.add_argument("--date", default=None, help="YYYY-MM-DD (default: today in Japan)")
    nb.add_argument("--text", action="store_true",
                    help="also dump the page as text, to write a new rule from")
    nb.set_defaults(func=cmd_npb_probe)

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
    t.add_argument("--league", choices=LEAGUES, default=None,
                   help="only test this league's channel "
                        "(default: every league)")
    t.add_argument("--ruler", action="store_true",
                   help="send a numbered ruler instead, to measure how many "
                        "lines your phone actually shows")
    t.set_defaults(func=cmd_test)

    args = p.parse_args(argv)
    if getattr(args, "year", None) is None and args.cmd == "check":
        args.year = today_tw()[:4]
    _setup_logging(args.verbose)
    cfg = config_mod.load(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
