"""Turning a stream of per-pitch assessments into a sane number of alerts.

The live log emits one row per pitch, so a single rally re-states the same
situation four or five times. We alert on *changes*, not on pitches:

  * fire when a game first crosses into the ``alert`` tier;
  * re-fire only when the situation materially escalates (LI climbs by
    ``escalation_step``);
  * re-arm only when the half-inning changes, not on a momentary dip -- an
    out mid-rally drops LI below the alert tier, and without this
    hysteresis a single inning would buzz three times as the rally rebuilt;
  * never fire twice for the same pitch (``GameState.pitch_id`` watermark),
    which also means a row the official scorer edits later will not re-alert.

The watermark is only as good as the identity behind it, and CPBL's ``Pkno``
is *not* an identity -- see :attr:`GameState.pitch_id`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .leverage import Assessment
from .models import GameState

ESCALATION_STEP = 1.0


@dataclass
class GameTracker:
    """Per-game alert memory."""

    escalation_step: float = ESCALATION_STEP
    seen_pitches: set[str] = field(default_factory=set)
    last_alert_leverage: float | None = None
    last_alert_key: tuple | None = None
    rally_half: tuple | None = None

    @staticmethod
    def _key(state: GameState) -> tuple:
        return (state.inning, state.is_top, state.outs, state.base_code(),
                state.visiting_score, state.home_score)

    @staticmethod
    def _half(state: GameState) -> tuple:
        return (state.inning, state.is_top)

    def should_fire(self, state: GameState, assessment: Assessment) -> bool:
        """Decide whether this pitch's situation deserves a notification."""
        # A pitch we've already processed (including a scorer's later edit).
        pitch_id = state.pitch_id
        if pitch_id in self.seen_pitches:
            return False
        self.seen_pitches.add(pitch_id)

        half = self._half(state)
        if self.rally_half is not None and half != self.rally_half:
            # New half-inning: forget the previous rally entirely.
            self.rally_half = None
            self.last_alert_leverage = None
            self.last_alert_key = None

        if not assessment.should_alert:
            return False

        key = self._key(state)
        if self.rally_half is None:
            self.rally_half = half
            self.last_alert_leverage = assessment.leverage
            self.last_alert_key = key
            return True

        # Already alerted this half-inning -- only a real escalation earns
        # another buzz.
        if key == self.last_alert_key:
            return False
        if (self.last_alert_leverage is not None
                and assessment.leverage >= self.last_alert_leverage + self.escalation_step):
            self.last_alert_leverage = assessment.leverage
            self.last_alert_key = key
            return True

        self.last_alert_key = key
        return False
