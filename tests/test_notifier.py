"""Alert formatting."""

import re

import pytest

from cpbl_alert import BRAND
from cpbl_alert.config import LEAGUES
from cpbl_alert.leverage import assess
from cpbl_alert.models import GameState, state_from_row
from cpbl_alert.notifier import (
    BREAK,
    DISCORD_MAX_RETRY_WAIT,
    JOIN,
    LINE_BUDGET,
    MAX_COLUMNS,
    RULER_LINES,
    RULER_WIDTH,
    ConsoleNotifier,
    DiscordNotifier,
    FanOutNotifier,
    TelegramNotifier,
    build_notifier,
    channel_for,
    cn_number,
    columns,
    diamond_rows,
    discord_markdown,
    format_alert,
    inning_label,
    outs_label,
    plain_text,
    ruler_text,
    situation,
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


# -- the diamond -----------------------------------------------------------
@pytest.mark.parametrize("kw,top,bottom", [
    ({}, "　◇　　", "◇　◇　"),
    ({"first": True}, "　◇　　", "◇　◆　"),
    ({"second": True}, "　◆　　", "◇　◇　"),
    ({"third": True}, "　◇　　", "◆　◇　"),
    ({"second": True, "third": True}, "　◆　　", "◆　◇　"),
    ({"first": True, "second": True, "third": True}, "　◆　　", "◆　◆　"),
])
def test_diamond_puts_each_runner_on_the_right_base(kw, top, bottom):
    """Second on top, third and first below -- the shape every scoreboard uses."""
    assert diamond_rows(_state(**kw)) == (top, bottom)


def test_diamond_rows_are_the_same_width_whatever_is_on_base():
    """The alignment invariant: this is what stops it reading as a staircase.

    Both rows are a fixed four-cell grid, so the text hung off the right of
    each starts in the same column -- and the diamond stays a diamond.
    """
    for kw in ({}, {"first": True}, {"second": True, "third": True},
               {"first": True, "second": True, "third": True}):
        top, bottom = diamond_rows(_state(**kw))
        assert columns(top) == columns(bottom) == 8, "four full-width cells"


# -- the notification ------------------------------------------------------
def test_alert_fits_the_measured_line_budget(game290):
    """Measured on a real phone with `test --ruler`, not guessed at."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    assert len(format_alert(st, assess(st)).split("\n")) == LINE_BUDGET


def test_no_line_is_wide_enough_to_wrap(game290):
    """A wrapped line costs the budget exactly like a written one.

    Every alert the fixture can produce is checked, not just one, because the
    long names are what would overflow -- 永田颯太郎 is why this test exists.
    """
    meta = game290["meta"]
    for row in game290["rows"]:
        st = state_from_row(row, meta)
        plain = re.sub(r"</?b>", "", format_alert(st, assess(st)))
        for line in plain.split("\n"):
            assert columns(line) <= MAX_COLUMNS, f"{line!r} would wrap"


def test_alert_text_contains_the_essentials(game290):
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert "台鋼" in text and "富邦" in text
    assert "台鋼雄鷹" not in text and "富邦悍將" not in text, "short names only"
    assert "4-5" in text
    assert "九上" in text and "兩出局" in text
    assert "◆◆" not in text, "bases are a diamond, not a run of glyphs"
    assert text.count("◆") == 3, "bases loaded"
    assert "關鍵度 LI" in text
    assert "平均=1.00" in text
    assert st.batter in text and st.pitcher in text


def test_the_brand_is_not_in_the_body(game290):
    """Telegram titles the notification with the bot's name; twice is a waste.

    Verified against the live API: the bot's display name is 快轉台 and the
    chat is private, so that string is already on screen above line 1.
    """
    st = state_from_row(game290["rows"][-1], game290["meta"])
    assert BRAND not in format_alert(st, assess(st))


def test_the_situation_is_one_joined_phrase(game290):
    """Inning and outs describe one moment, so they read as one.

    The bases are deliberately absent: the diamond says that, and repeating
    it would cost line 1 the width a long team name needs.
    """
    st = state_from_row(game290["rows"][-1], game290["meta"])
    assert situation(st) == f"九上{JOIN}兩出局"


def test_no_text_run_breaks_more_than_once(game290):
    """A break separates two kinds of thing; a run with two has no shape.

    Measured on the text only. The same character pads the diamond's grid,
    but there it is structure rather than punctuation -- which is exactly why
    the rule is stated about text runs and not about lines.

    A lock-screen preview strips bold, so this punctuation is the only
    hierarchy that survives to where the alert is actually read.
    """
    st = state_from_row(game290["rows"][-1], game290["meta"])
    first, second, third, fourth = format_alert(st, assess(st)).split("\n")
    top, bottom = diamond_rows(st)
    for run in (first, second.removeprefix(top), third.removeprefix(bottom),
                fourth):
        assert run.count(BREAK) <= 1, f"{run!r} breaks more than once"


def test_plain_text_still_reads_when_formatting_is_stripped(game290):
    """What the phone actually shows: no tags, no stray spacing artefacts."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    plain = re.sub(r"</?b>", "", format_alert(st, assess(st)))
    assert plain == ("台鋼 4-5 富邦　九上・兩出局\n"
                     "　◆　　打者 藍寅倫\n"
                     "◆　◆　投手 曾峻岳\n"
                     "關鍵度 LI 9.11　平均=1.00")


def test_alert_leads_with_the_scoreboard(game290):
    """Line one has to survive alone: a stacked notification may show only it."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    head = format_alert(st, assess(st)).split("\n")[0]
    assert head == "台鋼 <b>4-5</b> 富邦　九上・兩出局"


def test_the_matchup_rides_alongside_the_diamond(game290):
    """It costs no line of its own -- which is the only reason both fit."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    _, second, third, _ = format_alert(st, assess(st)).split("\n")
    top, bottom = diamond_rows(st)
    assert second == f"{top}打者 {st.batter}"
    assert third == f"{bottom}投手 {st.pitcher}"


def test_alert_drops_the_ptt_voice(game290):
    """The board register is gone. The diagram is not -- that is the UI."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    for gone in ("[LIVE]", "※", "推 ", "→", "緊張度"):
        assert gone not in text, f"{gone!r} should be gone from the alert"


def test_alert_is_stable_for_the_same_pitch(game290):
    """No variant picking any more: one situation, one sentence."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    assert format_alert(st, assess(st)) == format_alert(st, assess(st))


# -- the ruler -------------------------------------------------------------
def test_ruler_is_numbered_one_per_line():
    """Reading the last legible number off a lock screen is the whole point."""
    lines = ruler_text().split("\n")
    assert len(lines) == RULER_LINES
    assert [ln.split(" ")[0] for ln in lines] == [str(i) for i in range(1, RULER_LINES + 1)]


def test_ruler_lines_are_the_same_width():
    """A ruler whose lines differed would measure width, not line count."""
    widths = {len(ln.split(" ", 1)[1]) for ln in ruler_text().split("\n")}
    assert widths == {RULER_WIDTH + 1}, "the ┤ end marker plus the rule itself"


def test_ruler_marks_where_a_line_ends():
    """The marker is how a wrap shows up: it lands on a row of its own."""
    assert all(ln.endswith("┤") for ln in ruler_text().split("\n"))


def test_ruler_carries_no_markup():
    """It measures the phone, so nothing may render differently than it counts."""
    assert "<" not in ruler_text()


# -- delivery --------------------------------------------------------------
WEBHOOK = "https://discord.com/api/webhooks/111/tok"
OTHER = "https://discord.com/api/webhooks/222/tok"


class _Response:
    """Just enough of a requests response for the notifiers to read."""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self.headers = {}
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Sink:
    """A notifier that reports whatever it was told to."""

    key = ("sink",)
    label = "sink"

    def __init__(self, ok):
        self.ok = ok
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return self.ok


def test_falls_back_to_console_without_credentials():
    assert isinstance(build_notifier({}), ConsoleNotifier)


def test_telegram_used_when_configured():
    n = build_notifier({"telegram_token": "t", "telegram_chat_id": "1"})
    assert isinstance(n, TelegramNotifier)


def test_discord_used_when_configured():
    n = build_notifier({"discord_webhook": WEBHOOK})
    assert isinstance(n, DiscordNotifier)


def test_both_get_the_alert_when_both_are_configured():
    """Telegram and Discord are not alternatives -- you can want both."""
    n = build_notifier({"telegram_token": "t", "telegram_chat_id": "1",
                        "discord_webhook": WEBHOOK})
    assert isinstance(n, FanOutNotifier)
    assert [type(s) for s in n.sinks] == [TelegramNotifier, DiscordNotifier]


# -- one league, one channel ----------------------------------------------
def test_a_league_channel_beats_the_common_one():
    """The whole point: 中職 in one channel, 大聯盟 and 日職 in others."""
    cfg = {"discord_webhook": WEBHOOK,
           "discord_webhook_mlb": WEBHOOK.replace("111", "222"),
           "discord_webhook_npb": WEBHOOK.replace("111", "333")}
    assert build_notifier(cfg, "cpbl").webhook_url == WEBHOOK
    assert build_notifier(cfg, "mlb").webhook_url.endswith("/222/tok")
    assert build_notifier(cfg, "npb").webhook_url.endswith("/333/tok")


def test_every_league_can_be_routed():
    """A league in LEAGUES with no key of its own is one that cannot be split."""
    for league in LEAGUES:
        cfg = {"discord_webhook": WEBHOOK, f"discord_webhook_{league}": OTHER}
        assert build_notifier(cfg, league).webhook_url == OTHER
        for other in (lg for lg in LEAGUES if lg != league):
            assert build_notifier(cfg, other).webhook_url == WEBHOOK


def test_leagues_share_the_common_channel_when_they_have_no_own():
    """The setup everyone starts with: one channel, every league."""
    cfg = {"discord_webhook": WEBHOOK}
    assert len({build_notifier(cfg, lg).key for lg in LEAGUES}) == 1


def test_a_league_channel_is_invisible_to_the_other_league():
    """Configuring only MLB's channel must not leave CPBL shouting into it."""
    cfg = {"discord_webhook_mlb": WEBHOOK}
    assert isinstance(build_notifier(cfg, "cpbl"), ConsoleNotifier)
    assert isinstance(build_notifier(cfg, "mlb"), DiscordNotifier)


def test_telegram_chats_split_by_league_too():
    """A chat is a channel; the same routing has to reach it."""
    cfg = {"telegram_token": "t", "telegram_chat_id": "1",
           "telegram_chat_id_cpbl": "2"}
    assert build_notifier(cfg, "cpbl").chat_id == "2"
    assert build_notifier(cfg, "mlb").chat_id == "1"


def test_no_league_asked_means_the_common_channel():
    """Commands that watch no particular league get the shared setup."""
    cfg = {"discord_webhook": WEBHOOK, "discord_webhook_mlb": "x"}
    assert build_notifier(cfg).webhook_url == WEBHOOK
    assert channel_for(cfg, "discord_webhook", None) == WEBHOOK


def test_a_blank_league_channel_falls_through():
    """An untouched key from config.example.json is not a channel."""
    cfg = {"discord_webhook": WEBHOOK, "discord_webhook_cpbl": "   "}
    assert build_notifier(cfg, "cpbl").webhook_url == WEBHOOK


# -- what Discord is sent --------------------------------------------------
def test_discord_gets_markdown_not_html():
    assert discord_markdown("台鋼 <b>4-5</b> 富邦") == "台鋼 **4-5** 富邦"


def test_unbalanced_markup_is_stripped_rather_than_rewritten():
    """A dangling ** would swallow the rest of the message."""
    assert discord_markdown("台鋼 <b>4-5 富邦") == "台鋼 4-5 富邦"


def test_the_alert_survives_the_rewrite_line_for_line(game290):
    """The layout is the product; only the markup may differ by channel."""
    st = state_from_row(game290["rows"][-1], game290["meta"])
    text = format_alert(st, assess(st))
    assert plain_text(discord_markdown(text)).replace("**", "") == plain_text(text)
    assert len(discord_markdown(text).split("\n")) == LINE_BUDGET


def test_discord_alert_carries_no_html():
    st = _state(inning=9, outs=2, first=True, visiting_score=4, home_score=5,
                batter="魔鷹", pitcher="曾峻岳")
    assert "<b>" not in discord_markdown(format_alert(st, assess(st)))


def test_discord_never_pings_the_room(monkeypatch):
    """A player name must not be able to notify everyone in the channel."""
    posted = {}

    def fake_post(url, json=None, timeout=None):
        posted["url"], posted["json"] = url, json
        return _Response(204)

    monkeypatch.setattr("cpbl_alert.notifier.requests.post", fake_post)
    assert DiscordNotifier(WEBHOOK).send("@everyone <b>x</b>") is True
    assert posted["url"] == WEBHOOK
    assert posted["json"]["allowed_mentions"] == {"parse": []}
    assert posted["json"]["content"] == "@everyone **x**"


def test_discord_reports_a_rejected_webhook(monkeypatch):
    monkeypatch.setattr("cpbl_alert.notifier.requests.post",
                        lambda *a, **kw: _Response(404, text="Unknown Webhook"))
    assert DiscordNotifier(WEBHOOK).send("x") is False


def test_discord_waits_out_a_rate_limit_and_retries(monkeypatch):
    """A rally fires alerts seconds apart; one 429 must not drop one."""
    replies = [_Response(429, payload={"retry_after": 0.2}), _Response(204)]
    slept = []
    monkeypatch.setattr("cpbl_alert.notifier.requests.post",
                        lambda *a, **kw: replies.pop(0))
    monkeypatch.setattr("cpbl_alert.notifier.time.sleep", slept.append)
    assert DiscordNotifier(WEBHOOK).send("x") is True
    assert slept == [0.2] and not replies


def test_a_rate_limit_wait_is_capped(monkeypatch):
    """An alert that lands a minute late is about a moment already gone."""
    replies = [_Response(429, payload={"retry_after": 600}), _Response(204)]
    slept = []
    monkeypatch.setattr("cpbl_alert.notifier.requests.post",
                        lambda *a, **kw: replies.pop(0))
    monkeypatch.setattr("cpbl_alert.notifier.time.sleep", slept.append)
    DiscordNotifier(WEBHOOK).send("x")
    assert slept == [DISCORD_MAX_RETRY_WAIT]


def test_webhook_token_stays_out_of_the_label():
    """The label goes into logs and onto a terminal; the token lets you post."""
    label = DiscordNotifier(WEBHOOK).label
    assert "111" in label and "tok" not in label


# -- fanning out -----------------------------------------------------------
def test_one_dead_channel_does_not_silence_the_other():
    good, bad = _Sink(True), _Sink(False)
    assert FanOutNotifier([bad, good]).send("x") is True
    assert good.sent == ["x"] and bad.sent == ["x"]


def test_a_fan_out_with_nothing_delivered_is_a_failure():
    assert FanOutNotifier([_Sink(False), _Sink(False)]).send("x") is False


def test_the_same_channel_twice_is_the_same_key():
    """``test`` uses this to send one message per channel, not per league."""
    cfg = {"discord_webhook": WEBHOOK}
    assert build_notifier(cfg, "cpbl").key == build_notifier(cfg, "mlb").key
    assert build_notifier({"discord_webhook": "other"}, "cpbl").key != \
        build_notifier(cfg, "cpbl").key
