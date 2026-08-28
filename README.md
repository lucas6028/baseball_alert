# 快轉台

> 有機會了，別錯過。

盯著中華職棒的實況，在比賽真正緊張的時候推一則 Telegram 給你——九局滿壘、
一分差的追平分上壘、八局撕破平手——大比分落後和無聊的半局則完全安靜。

The name is the notification: on a lock screen, **快轉台** *is* the message,
and the score below it is just the detail.

```
快轉台　台鋼 4-5 富邦
九上・一出局・滿壘　心跳指數 89
```

通知只有兩行，而且兩行都在做事。第一行是預覽一定看得到的那行，要能單獨成立：
誰打誰、比數多少。第二行是它為什麼響——場面，然後是決定它響的那個數字。

再多就是寫給沒有人看的：你不是轉台就是不轉，而那個決定只需要比數、局面和
心跳指數多大聲。壘包圖、打者、投手、一排愛心，都要花一眼卻不會改變答案，
所以都拿掉了。

用字刻意平鋪直敘：講 **台鋼** 不講「台鋼雄鷹」，因為短名才塞得進一行；
講 **滿壘** 不畫壘包圖，因為那是同一件事的兩個字。對不上的隊名（季後賽、
明星賽、改名）原樣輸出，不會變成空字串。

短到只剩兩行的東西，好不好看就是斷句好不好。這裡只有兩種記號，而且都在做事：
全形空白 `　` 是**大斷點**，每行剛好一個——第一行隔開品牌與比分，第二行隔開
場面與指數，兩行因此對得起來，而不只是疊在一起；`・` 則把「局數、出局數、
壘包」黏成一句，因為那三件事講的是同一個瞬間。

粗體只是進 app 之後的加分：鎖定畫面的預覽會把格式吃掉，所以節奏必須在純文字
下就成立——這也是為什麼工作交給記號，`<b>` 只負責補強。

**命名**：產品名就是 **快轉台**，沒有英文對應名——它不需要一個。
Package 與 CLI 用純描述性的 `cpbl_alert`，讓陌生人一眼看懂這是什麼、也搜得到。
分數叫 **心跳指數**。

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
attaching in the 7th never replays the whole game at you. And only the
half-inning the game is actually in may reach your phone: a rally that both
started and ended between two polls is over, and telling you to turn the TV on
for it would be a lie.

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
- **The live log is regenerated, and its row keys with it.** Every ~60–90
  seconds the server rebuilds the whole log; each rebuild re-mints `Pkno` and
  `CreateTime` on *every* row. Two polls either side of a rebuild share zero
  Pknos out of 185 rows, and re-fetching a game that finished days ago returns
  Pknos different from the ones captured in the fixture. `MainEventNo`
  (`0610008000` = inning 06, top, 8th event) is structural and does survive, so
  that — never `Pkno` — is what identifies a pitch (`GameState.pitch_id`).
  Getting this wrong is not subtle: the watermark forgets the game once a
  minute and the same rally is pushed to your phone all night.
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
.venv/Scripts/python.exe -m pytest tests/ -q     # 77 tests
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
