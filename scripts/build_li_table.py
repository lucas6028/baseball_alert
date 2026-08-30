"""Regenerate the compact Leverage Index table bundled with cpbl-alert.

The source table is produced by Greg Stoll's open-source Retrosheet model:
https://github.com/gregstoll/baseballstats/blob/master/processleveragefromcumulative.py

Run this script from the repository root and apply its stdout to
``cpbl_alert/li_table.py``. Only regulation innings and score differences
from -8 through +8 are bundled; wider margins are effectively decided and
quiet in the alerting application.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import subprocess
import sys


REPOSITORY = "gregstoll/baseballstats"
SOURCE_PATH = "statsyears/leverage"
MIN_RUN_DIFF = -8
MAX_RUN_DIFF = 8


def source_text(payload_text: str | None = None) -> str:
    if payload_text is None:
        response = subprocess.run(
            ["gh", "api", f"repos/{REPOSITORY}/contents/{SOURCE_PATH}"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload_text = response.stdout
    payload = json.loads(payload_text)
    return base64.b64decode(payload["content"]).decode("utf-8")


def main() -> None:
    payload_text = sys.stdin.read() if "--stdin" in sys.argv else None
    values: dict[tuple[str, int, int, int], dict[int, float]] = {}
    for side, inning, outs, runners, run_diff, leverage in csv.reader(
        io.StringIO(source_text(payload_text))
    ):
        inning_i, diff_i = int(inning), int(run_diff)
        if inning_i > 9 or not MIN_RUN_DIFF <= diff_i <= MAX_RUN_DIFF:
            continue
        key = (side, inning_i, int(outs), int(runners))
        values.setdefault(key, {})[diff_i] = float(leverage)

    print('"""Generated MLB Leverage Index values derived from Retrosheet data.\n')
    print("Do not edit by hand; see scripts/build_li_table.py.\n")
    print('Keys are (batting side, inning, outs, base-state bitmask + 1)."""\n')
    print("LI_TABLE = {")
    for key in sorted(values):
        row = values[key]
        cells = ", ".join(
            f"{row.get(diff, 0.0):.2f}"
            for diff in range(MIN_RUN_DIFF, MAX_RUN_DIFF + 1)
        )
        print(f"    {key!r}: ({cells}),")
    print("}")


if __name__ == "__main__":
    main()
