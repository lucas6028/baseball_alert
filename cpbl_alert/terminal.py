"""Small, dependency-free helpers for optional terminal colour."""

from __future__ import annotations

import os
import sys

BOLD = "1"
DIM = "2"
UNDERLINE = "4"
RED = "31"
GREEN = "32"
YELLOW = "33"
BLUE = "34"
MAGENTA = "35"
CYAN = "36"


def supports_color(stream=None) -> bool:
    """Use ANSI colour only when requested or when stdout is interactive."""
    if "NO_COLOR" in os.environ:
        return False
    forced = os.environ.get("FORCE_COLOR")
    if forced is not None:
        return forced != "0"
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, *styles: str, enabled: bool = True) -> str:
    if not enabled or not styles:
        return text
    return f"\033[{';'.join(styles)}m{text}\033[0m"
