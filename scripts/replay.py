"""Replay a captured game through the leverage model -- offline, no network.

For tuning thresholds against a saved fixture. To replay a real game straight
from the CPBL site instead, use ``cpbl-alert check <gameSno>``.

Usage:
    uv run python scripts/replay.py [fixture.json] [--threshold=N] [--all]
"""

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpbl_alert.dedupe import GameTracker      # noqa: E402
from cpbl_alert.leverage import assess         # noqa: E402
from cpbl_alert.models import state_from_row   # noqa: E402

DEFAULT_FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "game290.json")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fixture", nargs="?", default=DEFAULT_FIXTURE)
    p.add_argument("--threshold", type=float, default=55.0)
    p.add_argument("--all", action="store_true", help="show every pitch")
    args = p.parse_args()

    with io.open(args.fixture, encoding="utf-8") as fh:
        data = json.load(fh)
    meta, rows = data["meta"], data["rows"]

    print(f"{meta['VisitingTeamName']} {meta['VisitingScore']} - "
          f"{meta['HomeScore']} {meta['HomeTeamName']}  ({len(rows)} pitches)\n")

    tracker = GameTracker()
    tiers = {"alert": 0, "watch": 0, "quiet": 0}
    fired = 0
    peak = None

    for row in rows:
        st = state_from_row(row, meta)
        a = assess(st, threshold=args.threshold)
        tiers[a.tier] += 1
        if peak is None or a.tension > peak[1].tension:
            peak = (st, a)
        hit = tracker.should_fire(st, a)
        if hit:
            fired += 1
        if args.all or hit:
            mark = "FIRE " if hit else f"{a.tier:<5}"
            print(f"  [{a.tension:5.1f}] {mark} {st.describe():<32} "
                  f"{st.batter[:5]:<6} | {' / '.join(a.reasons)}")

    total = len(rows)
    print(f"\nnotifications sent: {fired}")
    print(f"pitches: {total}   alert-tier: {tiers['alert']} "
          f"({100 * tiers['alert'] / total:.1f}%)   "
          f"watch: {tiers['watch']}   quiet: {tiers['quiet']}")
    if peak:
        st, a = peak
        print(f"peak tension {a.tension} -> {st.describe()} ({' / '.join(a.reasons)})")


if __name__ == "__main__":
    main()
