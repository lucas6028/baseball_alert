"""Alert formatting."""

from cpbl_alert.leverage import assess
from cpbl_alert.models import state_from_row
from cpbl_alert.notifier import ConsoleNotifier, build_notifier, format_alert


def test_alert_text_contains_the_essentials(game290):
    meta = game290["meta"]
    st = state_from_row(game290["rows"][-1], meta)
    text = format_alert(st, assess(st))
    assert "台鋼雄鷹" in text and "富邦悍將" in text
    assert "9局上" in text
    assert "心跳指數" in text
    assert st.batter in text


def test_alert_leads_with_the_brand(game290):
    """On a lock screen the product name is the message, so it goes first."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert text.startswith("<b>快轉台</b>")
    assert "緊張度" not in text, "old score label should be gone"


def test_bases_diagram_reflects_occupancy(game290):
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert text.count("◆") == 3        # bases loaded
    assert "◇" not in text


def test_falls_back_to_console_without_credentials():
    assert isinstance(build_notifier({}), ConsoleNotifier)


def test_telegram_used_when_configured():
    from cpbl_alert.notifier import TelegramNotifier
    n = build_notifier({"telegram_token": "t", "telegram_chat_id": "1"})
    assert isinstance(n, TelegramNotifier)
