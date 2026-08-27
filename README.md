# 快轉台

> 有機會了，別錯過。

盯著中華職棒的實況，在比賽真正緊張的時候推一則 Telegram 給你——九局滿壘、
一分差的追平分上壘、八局撕破平手——大比分落後和無聊的半局則完全安靜。

The name is the notification: on a lock screen, **快轉台** *is* the message,
and the score below it is just the detail.

```
快轉台　[LIVE] 台鋼 4-5 富邦
九上　一出局　●○

　◆
◆　◆

打者　魔鷹
投手　曾峻岳

心跳指數 89　這篇會爆　♥♥♥♥♥♥♥♥♥♡
※ 發信站: 快轉台(ptt.cc)
推 壘包上站滿人，就等這一棒
→ 一出局，別打成雙殺就好
→ 九上，沒有下一次了
推 追平分已經站上壘包，逆轉就靠這棒
推 別當烏鴉，安靜轉台就好
```

通知是用 PTT 棒球板的話寫的——`[LIVE]` 開頭、`※ 發信站` 收尾、理由用推文
排下來。這不是裝飾：會裝這個東西的人本來就在板上看球，用板上的講法寫，讀起來
是「有人已經在看了」，而不是聯盟發的新聞稿。

**命名**：產品名就是 **快轉台**，沒有英文對應名——它不需要一個。
Package 與 CLI 用純描述性的 `cpbl_alert`，讓陌生人一眼看懂這是什麼、也搜得到。
分數叫 **心跳指數**。

## 通知的口氣

`cpbl_alert/ptt.py` 是唯一放俚語的地方，規則有四條：

- **隊名用板上的寫法。** 沒有人在直播文裡打「統一7-ELEVEn獅」，就是「統一」。
  對不上的隊名（季後賽、明星賽、改名）原樣輸出，不會變成空字串。
- **俚語要扛數字。** 「落後3分，說是垃圾時間也不為過」裡的 3 分還在，
  想看原始數字的人不必猜。噓的用法跟板上一樣：留給守成和垃圾時間。
- **每個詞都綁著讓它成立的條件。** 板上的黑話幾乎都內建一個場面：「劇場」是
  *終結者* 守不住領先，所以要九局以後、追平分又已經上壘；「開魯閣」是掉滿十分，
  所以要記分板上真的有十分；「問天」是自家先發沒有得到火力支援，所以講的是
  **先發**，絕不是 `state.pitcher`——那是對面的投手。詞脫離了場面，就是外行話，
  所以條件寫在 `_EXTRAS` 裡，跟句子放在一起。
- **不拿真人開玩笑。** 板上的詞典有很多是從某個球員的一個壞夜晚長出來的
  （國慶球、國輝球、玉山大曲、院長、瓜、坦），也有球迷蔑稱和裁判黑哨指控。
  那些都是真的俚語，這裡刻意不收：這是推到鎖定畫面的通知，讀者沒有答應要看
  一個指名道姓的職業球員被當梗。留下來的都是描述 **場面** 的——劇場、問天、
  殘壘、坐牢、大中計。

前三條都有測試把關 (`tests/test_ptt.py`)：每個借來的詞都從兩邊測，
該出現的場面要叫得出來，不該出現的場面要叫不出來。第四條由一份
黑名單掃過 `ptt.all_phrases()`。

分析文字不跟著變。`cpbl-alert check` 印的是 `Assessment.reasons`，那是調
threshold 用的，維持原本的中性寫法；PTT 版只在通知裡出現，而且是從
`closeness_tag` 對照過來的，不會自己重推一次追平分的邊界。

同一顆球永遠產生同一句（變化用 CRC 挑，不用 `random`），所以 replay 和測試
都可重現，連續兩則通知又不會長得一樣。要加句子的話：無條件成立的放進
`_BASES` / `_OUTS` / `_LATE` / `_CLOSENESS`，有前提的放進 `_EXTRAS` 並寫上
`when`。俚語是跟同一行的其他說法**競爭**，不是多加一行——通知的行數由場面決定，
免得滿壘九局把比分擠出手機預覽。

## Quick start

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Linux/mac: .venv/bin/python

cp config.example.json config.json      # then fill in your bot token
.venv/Scripts/python.exe -m cpbl_alert.cli chat-id   # find your chat id
.venv/Scripts/python.exe -m cpbl_alert.cli test      # send a test message
.venv/Scripts/python.exe -m cpbl_alert.cli run       # start watching
```

### Getting a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Put the token in `config.json` (or set `TELEGRAM_TOKEN`).
3. Send your new bot any message.
4. Run `cpbl-alert chat-id` and copy the id into `config.json`.

## Commands

| Command | What it does |
|---|---|
| `run` | Watch today's live games and push alerts. `--dry-run` prints instead, `--once` does a single pass |
| `live` | List today's games with real status (pending / LIVE / final) |
| `check <gameSno>` | Replay a real game through the model and show what *would* have fired — the way to tune your threshold |
| `test` | Send a test notification |
| `chat-id` | Look up your Telegram chat id |

Tuning against a game you actually watched is the fastest way to find your
threshold:

```bash
python -m cpbl_alert.cli check 290 --threshold 50
python -m cpbl_alert.cli check 290 --all      # every pitch, with scores
```

## Configuration

`config.json` (environment variables win, which keeps tokens out of files):

| Key | Env | Default | Meaning |
|---|---|---|---|
| `telegram_token` | `TELEGRAM_TOKEN` | — | Bot token from BotFather |
| `telegram_chat_id` | `TELEGRAM_CHAT_ID` | — | Where to send |
| `threshold` | `CPBL_THRESHOLD` | `55` | 心跳指數 that triggers an alert |
| `poll_seconds` | — | `15` | Seconds between polls (floored at 10) |
| `teams` | `CPBL_TEAMS` | `[]` | Only alert on these teams; empty means all |

Without credentials it falls back to printing alerts to the console, so you can
try it before setting up a bot.

## How a situation is scored

Every pitch produces a **心跳指數** (heartbeat index) from 0–100, the product
of three factors:

**1. Situation** — how dangerous the base/out state is. This blends two
standard tables: expected runs (RE24) and the probability of scoring *at least
one* run. The blend matters. Late in a tight game you don't need runs plural,
you need one, and those two curves disagree sharply — a runner on third with
one out is mediocre by expected runs and enormous by "will he score". Weighting
only by expected runs misses exactly the moments people care about most.

**2. Urgency** — a multiplier that rises through the game, from 0.55 in the
1st to 1.15 in the 9th. Extra innings inherit the 9th-inning weight.

**3. Closeness** — the score-margin gate, built around the *tying run*. With
R runners on base, the batter is potential run R+1, so trailing by ≤R means the
tying run is already on base (full weight); trailing by R+1 puts it at the
plate; further back decays toward a floor. Leading decays with the size of the
lead. This is what keeps a runner on second in a 9-run blowout off your phone
while the identical situation in a one-run game lights it up.

### Alert volume

Replaying a full day of real CPBL baseball (three games, 886 pitches):

| Game | Result | Alerts |
|---|---|---|
| #288 樂天桃猿 0–2 中信兄弟 | scoreless into the 8th | 1 |
| #289 味全龍 0–1 統一7-ELEVEn獅 | pitchers' duel | 1 |
| #290 台鋼雄鷹 4–5 富邦悍將 | 7th-inning rally, 9th-inning comeback attempt | 4 |

Alerts fire on *changes*, not pitches. Within a half-inning you get one alert
when it crosses the threshold and another only if it materially escalates
(+10 心跳指數). A momentary dip — an out mid-rally — does not re-arm it; only a
new half-inning does. Starting mid-game primes on existing pitches first, so
attaching in the 7th never replays the whole game at you.

## Data source

There is no public CPBL API. This calls the same endpoints the official site's
own front-end uses. Three things about them are worth knowing, all handled in
`cpbl_alert/client.py`:

- **Two different anti-forgery tokens.** `/box/getlive` wants ASP.NET's
  `__RequestVerificationToken` as a *form field*, scraped from the `/box` page.
  `/schedule/getgamedatas` wants a different token as a
  `RequestVerificationToken` *header*, inlined in the `/schedule` page's
  JavaScript. Both expire and are re-scraped on rejection.
- **A CDN cookie challenge.** The first request to any path returns 307/308
  pointing back at the same path, setting a `__chtcdn` cookie. Replaying with
  the cookie works, so every request retries.
- **Game status is not in the schedule.** The schedule payload's `GameStatus`
  is always null — it only tells you a game exists and when it starts. Whether
  a game is live comes from `getlive` → `CurtGameDetailJson.GameStatus`
  (2 = live, 3 = final, 8 = delayed).

The season schedule is a ~440KB payload, so it's fetched once and cached; only
games whose start time has passed get polled, and a game that has finished is
not polled again for the rest of the day.

**Be polite.** This is someone else's website, not a paid API. The poll
interval is floored at 10 seconds, requests are gzipped and throttled, and
games that haven't started aren't touched. Don't lower the floor.

### Live-log field semantics (the off-by-one that matters)

Determined empirically against a full real game — see `test_out_count_is_pre_pitch`:

- One row = one pitch.
- `OutCnt` and the base fields are **pre-pitch**: they describe the situation
  the pitch was thrown into. An out shows up on the *next* row (verified on 32
  of 34 out-producing pitches).
- `StrikeCnt` / `BallCnt` are **post-pitch**: the pitch's own ball or strike is
  already applied.

So the newest row is the state going *into* the most recent pitch, and a chance
created by a hit becomes visible one pitch later. The official CPBL site's own
scoreboard widget has exactly the same lag. Getting this backwards would put
every threshold off by one pitch.

## Development

```bash
.venv/Scripts/python.exe -m pytest tests/ -q     # 670 tests
python scripts/replay.py --all                   # offline, against the fixture
```

`tests/fixtures/game290.json` is a real captured game (324 pitches, 台鋼雄鷹
4–5 富邦悍將 on 2026-08-26) — the tests run against real data, including the
9th-inning bases-loaded rally.

## Known limits

- **CPBL only.** Adding a league means a new client; the model and notifier are
  league-agnostic.
- **No win-probability model.** 心跳指數 is a transparent heuristic built from
  public run-expectancy tables, not a trained CPBL win-probability model. It has
  no notion of who is pitching, who is due up, or bullpen state.
- **Run-expectancy tables are MLB-derived.** CPBL's run environment differs;
  only the relative ordering is used, which is stable, but the absolute numbers
  aren't CPBL-calibrated.
- **One-pitch lag**, as described above.
- **Regular season (`kindCode=A`) by default.** Postseason uses different codes.
- **Unofficial endpoints** — if CPBL changes its site, this breaks.
