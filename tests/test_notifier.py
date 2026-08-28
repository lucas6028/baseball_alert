"""Alert formatting."""

import pytest

from cpbl_alert.leverage import assess
from cpbl_alert.models import GameState, state_from_row
from cpbl_alert.notifier import (
    ConsoleNotifier,
    bases_label,
    build_notifier,
    cn_number,
    format_alert,
    inning_label,
    outs_label,
    team,
)


def _state(**kw) -> GameState:
    base = dict(
        game_sno=1, year="2026", kind_code="A", inning=1, is_top=True, outs=0,
        first=False, second=False, third=False, balls=0, strikes=0,
        visiting_score=0, home_score=0, batter="", pitcher="", event_no="x",
        created_at="", visiting_team="台鋼雄鷹", home_team="富邦悍將",
    )
    base.update(kw)
    return GameState(**base)


# -- vocabulary ------------------------------------------------------------
def test_teams_use_their_short_names():
    assert team("統一7-ELEVEn獅") == "統一"
    assert team("中信兄弟") == "兄弟"
    assert team("台鋼雄鷹") == "台鋼"


def test_unknown_team_keeps_its_full_name():
    """Postseason and all-star sides are not in the table; never blank them."""
    assert team("中華隊") == "中華隊"
    assert team("") == ""


def test_inning_and_outs_read_like_a_scoreboard():
    assert inning_label(_state(inning=4, is_top=True)) == "四上"
    assert inning_label(_state(inning=9, is_top=False)) == "九下"
    assert cn_number(12) == "十二"        # extra innings still read as words
    assert inning_label(_state(inning=12, is_top=True)) == "十二上"
    assert outs_label(0) == "無人出局"
    assert outs_label(2) == "兩出局"


@pytest.mark.parametrize("kw,expected", [
    ({}, "壘上無人"),
    ({"first": True}, "一壘有人"),
    ({"second": True, "third": True}, "二三壘"),
    ({"first": True, "second": True, "third": True}, "滿壘"),
])
def test_bases_read_as_words_not_a_diagram(kw, expected):
    assert bases_label(_state(**kw)) == expected


# -- the notification ------------------------------------------------------
def test_alert_is_two_lines(game290):
    """A phone preview shows about two lines; anything past them is for nobody."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert len(text.split("\n")) == 2


def test_alert_text_contains_the_essentials(game290):
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert "台鋼" in text and "富邦" in text
    assert "台鋼雄鷹" not in text and "富邦悍將" not in text, "short names only"
    assert "4-5" in text
    assert "九上" in text and "兩出局" in text and "滿壘" in text
    assert "心跳指數" in text


def test_alert_leads_with_the_brand_and_the_score(game290):
    """On a lock screen the product name is the message, so it goes first.

    The first line has to survive alone, because it is the one a truncated
    preview is guaranteed to show.
    """
    st = state_from_row(game290["rows"][-1], game290["meta"])
    head, tail = format_alert(st, assess(st)).split("\n")
    assert head.startswith("<b>快轉台</b>")
    assert "台鋼 4-5 富邦" in head
    assert "心跳指數" in tail


def test_alert_drops_what_it_used_to_say(game290):
    """The PTT voice and the scoreboard block are gone, not merely shortened."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    for gone in ("[LIVE]", "※", "推 ", "→", "◆", "◇", "●", "♥", "打者", "投手",
                 "緊張度"):
        assert gone not in text, f"{gone!r} should be gone from the alert"


def test_alert_is_stable_for_the_same_pitch(game290):
    """No variant picking any more: one situation, one sentence."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    assert format_alert(st, assess(st)) == format_alert(st, assess(st))


# -- delivery --------------------------------------------------------------
def test_falls_back_to_console_without_credentials():
    assert isinstance(build_notifier({}), ConsoleNotifier)


def test_telegram_used_when_configured():
    from cpbl_alert.notifier import TelegramNotifier
    n = build_notifier({"telegram_token": "t", "telegram_chat_id": "1"})
    assert isinstance(n, TelegramNotifier)
