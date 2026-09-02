"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import threading

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
from .terminal import GREEN, RED, paint, supports_color
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
            print(f"  [LI {a.leverage:4.2f}] {mark} {st.describe():<32} "
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
        names = " / ".join(LEAGUE_LABELS.get(lg, lg.upper()) for lg in leagues)
        mark = "✓" if sent else "✗"
        colored_mark = paint(mark, GREEN if sent else RED, enabled=supports_color())
        print(f"  {colored_mark} {names:<16} → {notifier.label} "
              f"({'sent' if sent else 'failed'})")
        ok = ok and sent
    if args.ruler and ok:
        print(RULER_HELP)
    return 0 if ok else 1


def cmd_init(args, cfg) -> int:
    """Create or update the config interactively, then test delivery."""
    from .setup_wizard import run
    return run(args.config or config_mod.CONFIG_PATH)


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


def build_watcher(league: str, cfg, *, poll=None, threshold=None,
                  dry_run: bool = False):
    """The watcher for one league, built from config with CLI overrides.

    Pulled out of the three single-league commands so that ``all`` builds the
    same objects they do rather than a second, drifting copy of the wiring.
    Each league keeps its own poll default -- 15s against the CPBL API, 20s
    against MLB's, 30s against npb.jp, which is a web page and not an API --
    and that is why there is no shared poll setting for ``all`` to flatten
    them with.
    """
    notifier = build_notifier(cfg, league)
    if league == "cpbl":
        return Watcher(
            client=CpblClient(),
            notifier=notifier,
            threshold=float(threshold if threshold is not None else cfg["threshold"]),
            poll_seconds=int(poll or cfg["poll_seconds"]),
            teams=cfg.get("teams"),
            dry_run=dry_run,
        )
    if league == "mlb":
        return mlb.TaiwaneseWatcher(
            client=mlb.MlbClient(),
            notifier=notifier,
            extra_players=cfg.get("mlb_players"),
            poll_seconds=int(poll or cfg.get("mlb_poll_seconds") or 20),
            dry_run=dry_run,
        )
    if league == "npb":
        return npb.TaiwaneseWatcher(
            feed=npb.NpbClient(),
            notifier=notifier,
            extra_players=cfg.get("npb_players"),
            poll_seconds=int(poll or cfg.get("npb_poll_seconds")
                             or npb.DEFAULT_POLL_SECONDS),
            dry_run=dry_run,
        )
    raise ValueError(f"unknown league: {league}")


def watcher_summary(league: str, watcher) -> str:
    """The line a watcher prints on start: what it watches, where it lands."""
    if league == "cpbl":
        return (f"watching CPBL (threshold {watcher.threshold}, "
                f"poll {watcher.poll_seconds}s"
                + (f", teams {watcher.teams}" if watcher.teams else "") + ")"
                + f" -> {_destination(watcher)}")
    return (f"watching {league.upper()} for Taiwanese players "
            f"(poll {watcher.poll_seconds}s) -> {_destination(watcher)}")


def cmd_mlb(args, cfg) -> int:
    """Watch MLB and push when a Taiwanese player is on stage."""
    watcher = build_watcher("mlb", cfg, poll=args.poll, dry_run=args.dry_run)
    print(watcher_summary("mlb", watcher))
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
    watcher = build_watcher("npb", cfg, poll=args.poll, dry_run=args.dry_run)
    print(watcher_summary("npb", watcher))
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
        print(f"fixture lookup failed: {exc}")
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

    npb.jp publishes no live situation at all -- no current batter, no count,
    no runners -- so ``npb.py`` derives all of it from 最新のオーダー and the
    log of finished plate appearances. That derivation is testable but not
    self-evident, and this prints every step of it: what survived the header
    slice, what the line score said, both batting orders, the last event
    logged, what that event carries forward, and who it concludes is up.

    An empty ``order`` line or a ``NO RULE`` on the carry is the thing to
    look for -- either one means the page has changed under the rules.

    It also prints the day's fixtures and their 予告先発, which is the one
    thing about this side that could not be measured when it was written:
    npb.jp certainly names the starters of a game already under way, and
    whether it names them *before* first pitch decides whether the starter
    notice ever fires. Run this in the morning and the 先発 column answers it.
    """
    client = npb.NpbClient()
    day = args.date or npb.today_jst()
    try:
        if args.game:
            game_ids = [args.game if "/" in args.game
                        else f"{day[:4]}/{day[5:7]}{day[8:10]}/{args.game}"]
        else:
            game_ids = client.game_ids(day)
            print(f"fixtures {day} -> {len(game_ids)} game link(s): "
                  f"{', '.join(game_ids) or '(none)'}\n")
    except requests.RequestException as exc:
        print(f"fetch failed: {exc}")
        return 1

    try:
        # The whole day's links, not the one game asked for: whether a
        # fixture has started is a fact about the day, not about the argument.
        fixtures, started = client.fixtures(day), client.game_ids(day)
    except (npb.NpbError, requests.RequestException) as exc:
        print(f"fixture list failed: {exc}\n")
        fixtures, started = [], []
    if fixtures:
        print(f"the day's fixtures -- an empty 先発 before first pitch is the "
              f"open question:")
        for fixture in fixtures:
            when = (fixture.starts_at.strftime("%H:%M JST")
                    if fixture.starts_at else "--:--")
            note = "" if fixture.under_way(started) else "   (not started)"
            print(f"  {fixture.slug:<6} {fixture.away_team} at "
                  f"{fixture.home_team:<5} {when}  先発 "
                  f"{fixture.away_starter or '--'} / "
                  f"{fixture.home_starter or '--'}{note}")
        print()

    for game_id in game_ids:
        try:
            page = client.game_page(game_id)
        except requests.RequestException as exc:
            print(f"{game_id}: fetch failed: {exc}")
            continue
        block = npb.main_block(page)
        game = npb.parse_game_page(page, game_id)
        status, banner_inning, banner_top = npb.parse_status(block)
        away_code, home_code, away_runs, home_runs = npb.parse_linescore(block)
        halves = npb.parse_progress(block)
        away_lineup, home_lineup = npb.parse_order(block)

        print(f"== {game_id} ==  ({len(page)} bytes, {len(block)} after the "
              f"six-game header is cut away)")
        print(f"  banner      {status} "
              f"{banner_inning}{'表' if banner_top else '裏'}")
        print(f"  line score  {away_code or '?'} {away_runs} - {home_runs} "
              f"{home_code or '?'}  -> {game.away_team} at {game.home_team}")
        for side, lineup in (("away", away_lineup), ("home", home_lineup)):
            filled = "".join(f"{n}{lineup.slots[n].name} " for n in sorted(lineup.slots))
            print(f"  order {side}  {filled or '-- nothing matched'}"
                  f"| 投 {lineup.pitcher.name if lineup.pitcher else '--'}")
        print(f"  最新経過    {len(halves)} half-inning(s) logged")
        if halves and halves[-1].plays:
            last = halves[-1].plays[-1]
            made = npb.outs_made(last.result)
            print(f"    last      {halves[-1].inning}"
                  f"{'表' if halves[-1].is_top else '裏'} "
                  f"{last.outs}out {last.bases} "
                  f"{last.batter.name if last.batter else '(baserunning)'} "
                  f"-> {last.result!r}")
            print(f"    carries   +{made if made is not None else '?? NO RULE'} out(s), "
                  f"bases {npb.bases_after(last.bases, last.result)}")
        print(f"  inferred    打者 {game.batter or '-- could not place the order'} "
              f"(第{game.batting_order or '?'}棒)  次 {game.on_deck or '--'}  "
              f"投手 {game.pitcher or '--'}")
        state = npb.state_from_npb_game(game)
        print(f"  -> state    {state.describe() if state else '(not live)'}")
        if args.text:
            print("\n--- page text ---")
            print(npb.strip_tags(block))
        print()
    return 0


def cmd_run(args, cfg) -> int:
    watcher = build_watcher("cpbl", cfg, poll=args.poll,
                            threshold=args.threshold, dry_run=args.dry_run)
    print(watcher_summary("cpbl", watcher))
    try:
        watcher.run(once=args.once)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def _lane(league: str, watcher, once: bool = False,
          failed: set | None = None) -> None:
    """One league's loop, walled off so a crash in it cannot take the others.

    Each watcher guards its own network calls but not the processing that
    follows them, and in a three-league process an unhandled raise would
    otherwise be one lane going quiet while the other two carry on -- silence
    that looks exactly like a night with no games.

    Recorded in ``failed`` as well as logged, because a lane that ended by
    crashing must still be visible at exit: swallowing it here and returning 0
    would tell a supervisor this was a clean shutdown and leave it unrestarted.
    """
    try:
        watcher.run(once=once)
    except Exception as exc:  # noqa: BLE001 - one lane dying must not be silent
        logging.exception("%s watcher stopped: %s", league.upper(), exc)
        if failed is not None:
            failed.add(league)


def cmd_all(args, cfg) -> int:
    """Watch every league from one process, one thread per league.

    A thread each rather than one loop taking turns, because the three
    watchers do not share a clock and must not: they poll at different rates
    against three different sources, and the CPBL watcher in particular keeps
    per-game priming state inside its own ``run`` loop that a turn-taking
    scheduler would reset on every pass.

    "Only when there is a game" needs nothing new here -- it is already in
    each watcher's own pacing, and running them together inherits all of it:
    CPBL idles outside 16:00-24:00 Taiwan time, MLB polls when a game is live
    or first pitch is within half an hour, NPB when a game is live or pending
    inside 13:00-24:00 Japan time. Everywhere else all three sleep long.
    That beats a hardcoded window per league, so there is no window to set.
    """
    leagues = list(dict.fromkeys(args.league or LEAGUES))
    watchers = [(lg, build_watcher(lg, cfg, dry_run=args.dry_run))
                for lg in leagues]
    for league, watcher in watchers:
        print(watcher_summary(league, watcher))

    # A lane that ended by crashing is the exit code, whichever path ran.
    # Ctrl-C is not: that is the user ending it, and it exits clean.
    failed: set[str] = set()

    # One pass is a wiring check, and a wiring check reads better in order
    # than interleaved, so --once stays on this thread.
    if args.once:
        try:
            for league, watcher in watchers:
                _lane(league, watcher, once=True, failed=failed)
        except KeyboardInterrupt:
            print("\nstopped")
            return 0
        return 1 if failed else 0

    threads = [threading.Thread(target=_lane, args=(league, watcher),
                                kwargs={"failed": failed},
                                name=f"watch-{league}", daemon=True)
               for league, watcher in watchers]
    for thread in threads:
        thread.start()
    try:
        # Daemon threads plus a timed join: a bare join() would park the main
        # thread uninterruptibly, and Ctrl-C is delivered only there.
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
    return 1 if failed else 0


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

    a = sub.add_parser("all", help="watch all three leagues in one process")
    a.add_argument("--league", action="append", choices=LEAGUES, default=None,
                   help="watch only this league (repeatable; default: all three)")
    a.add_argument("--once", action="store_true", help="single pass, then exit")
    a.add_argument("--dry-run", action="store_true", help="print instead of pushing")
    a.set_defaults(func=cmd_all)

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

    i = sub.add_parser("init", help="interactively configure and test notifications")
    i.set_defaults(func=cmd_init)

    args = p.parse_args(argv)
    if getattr(args, "year", None) is None and args.cmd == "check":
        args.year = today_tw()[:4]
    _setup_logging(args.verbose)
    # The initializer reads the raw file itself. Loading first would apply
    # environment secrets and could accidentally persist them to config.json.
    cfg = {} if args.cmd == "init" else config_mod.load(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
