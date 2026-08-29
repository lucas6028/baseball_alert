# 快轉台

<p align="center">
  <img src="assets/readme-hero.png" alt="夜色球場中滿壘亮起，心跳訊號連向即時通知" width="100%">
</p>

> 有機會了，別錯過。

盯著中華職棒的實況，在比賽真正緊張的時候推一則 Telegram 給你——九局滿壘、
一分差的追平分上壘、八局撕破平手——大比分落後和無聊的半局則完全安靜。

也盯大聯盟，但盯的是另一件事：**台灣選手上場的那一刻**——鄧愷威登板、李灝宇站
上打擊區。同一支 bot、同樣四行，[往下看](#大聯盟台灣選手上場)。

通知的標題就是產品名——Telegram 用聊天室名稱當標題，而那就是 bot 的顯示名稱
**快轉台**。所以正文裡不再寫一次：那會用掉最稀有的一行，去講一個螢幕上已經有
的詞。

```
台鋼 4-5 富邦　九上・一出局
　◆　　打者 魔鷹
◆　◆　投手 曾峻岳
心跳指數 89　♥♥♥♥♥♥♥♥♥♡
```

```
洋基 3-4 巨人　七上・兩出局
　◆　　打者 Judge
◇　◇　投手 鄧愷威
台灣投手登板　今日 1.2局・3K・失1
```

## Quick start

Requires Python 3.10 or newer.

### Windows (PowerShell)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json

.venv/Scripts/python.exe -m cpbl_alert.cli chat-id   # find your chat id
.venv/Scripts/python.exe -m cpbl_alert.cli test      # send a test message
.venv/Scripts/python.exe -m cpbl_alert.cli run       # watch CPBL
.venv/Scripts/python.exe -m cpbl_alert.cli mlb       # watch MLB (in another terminal)
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.json config.json

.venv/bin/python -m cpbl_alert.cli chat-id
.venv/bin/python -m cpbl_alert.cli test
.venv/bin/python -m cpbl_alert.cli run
.venv/bin/python -m cpbl_alert.cli mlb       # in another terminal
```

Before running `chat-id`, fill in `telegram_token` in `config.json` (or set
`TELEGRAM_TOKEN`). The CPBL and MLB watchers are separate processes; run either
one or both depending on what you want to follow.

### Getting a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Put the token in `config.json` (or set `TELEGRAM_TOKEN`).
3. Send your new bot any message.
4. Run `python -m cpbl_alert.cli chat-id` and copy the id into `config.json`.

## Commands

| Command | What it does |
|---|---|
| `run` | Watch today's live games and push alerts. `--dry-run` prints instead, `--once` does a single pass |
| `live` | List today's games with real status (pending / LIVE / final) |
| `check <gameSno>` | Replay a real game through the model and show what *would* have fired — the way to tune your threshold |
| `test` | Send a test notification. `--ruler` sends a numbered ruler instead, so you can see how many lines your phone shows before it truncates |
| `chat-id` | Look up your Telegram chat id |
| `mlb` | Watch MLB and push when a Taiwanese player takes the plate or the mound. `--dry-run` and `--once` as above |
| `mlb-live` | List the MLB games in the current window, with who is batting and pitching in each |
| `mlb-players` | List the Taiwanese players MLB currently has on its books, and where each Chinese name comes from |

Tuning against a game you actually watched is the fastest way to find your
threshold:

```bash
python -m cpbl_alert.cli check 290 --threshold 50
python -m cpbl_alert.cli check 290 --all      # every pitch, with scores
```

## Configuration

`config.json` (environment variables win, which keeps tokens out of files).
Set `CPBL_ALERT_CONFIG` to use a config file at a different path:

| Key | Env | Default | Meaning |
|---|---|---|---|
| `telegram_token` | `TELEGRAM_TOKEN` | — | Bot token from BotFather |
| `telegram_chat_id` | `TELEGRAM_CHAT_ID` | — | Where to send |
| `threshold` | `CPBL_THRESHOLD` | `55` | 心跳指數 that triggers an alert |
| `poll_seconds` | — | `15` | Seconds between polls (floored at 10) |
| `teams` | `CPBL_TEAMS` | `[]` | Only alert on these teams; empty means all |
| `mlb_players` | `MLB_PLAYERS` | `[]` | Extra MLB players to treat as Taiwanese, as ids or full names. Nationality otherwise comes from the API, so this is only for someone it does not record as Taiwan-born |
| `mlb_poll_seconds` | — | `20` | Seconds between MLB polls (floored at 10) |

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

## Data sources

### CPBL

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

### MLB

MLB, unlike CPBL, has a real public JSON API, so `cpbl_alert/mlb.py` has no
scraping, no anti-forgery tokens and no CDN challenge in it. Three things about
it shaped the poller:

- **One request covers the whole league.**
  `/schedule?sportId=1&hydrate=linescore` returns *every* game's current batter,
  pitcher, bases, outs, count and score in one ~50KB payload (10KB gzipped). So
  a tick is a single request no matter how many games are on, which is what
  makes watching all fifteen affordable. Per-game `feed/live` would be 500KB
  apiece. The boxscore behind line four is a second request, made only when
  something is actually being sent.
- **Nationality is in the data.** `/sports/1/players` carries `birthCountry`,
  and MLB spells Taiwan `Taiwan`, so membership is a lookup rather than a
  hand-kept list — one request, cached for the game-day. The built-in name
  table is a *backstop* (a call-up the roster endpoint has not caught up with)
  and the source of Chinese names, which the API does not have at all.
- **`dates` is keyed on the US business date, and there are always two of
  them.** A 20:15 ET game on Aug 28 is filed under `2026-08-28` even though it
  starts at 00:15 UTC on the 29th — and 08:15 the next morning in Taiwan, which
  is when you would be watching it. So the window is two days wide and every
  `dates` entry is flattened together; reading `dates[0]` drops half the night.

Two smaller traps, both of which would put a wrong name on the phone:
`linescore.offense.pitcher` exists and is the *batting* team's pitcher of record
— the man on the mound is `linescore.defense.pitcher`. And between half-innings
the linescore reads `outs: 3` while still naming a batter and a pitcher, but
they are the pair from the half that just ended mixed with the one about to
start, so that state is skipped rather than read.

## Development

```bash
.venv/Scripts/python.exe -m pytest tests/ -q     # 132 tests
python scripts/replay.py --all                   # offline, against the fixture
```

`tests/fixtures/game290.json` is a real captured game (324 pitches, 台鋼雄鷹
4–5 富邦悍將 on 2026-08-26) — the tests run against real data, including the
9th-inning bases-loaded rally.

The MLB fixtures are real captures too. `mlb_schedule.json` is a two-day
window (32 games — finals, games in progress, games not yet started), because
the shape of that payload is the one thing this side cannot control.
`mlb_taiwanese_on_stage.json` and `mlb_boxscore.json` are the moment the
feature exists for, caught by polling the live API until it happened: 李灝宇 at
the plate against Tarik Skubal, 六下 no outs, runner on first, 1–1, 0 for 2 on
the day. One test carries that payload all the way to the four lines that
would have reached the phone, with the thirteen other games in it staying
silent. The sequences a capture cannot give you — the same pitcher still out
there a poll later, a batter coming up again two innings on — are built by
hand.

## Known limits

- **Two leagues, two commands.** `run` watches CPBL, `mlb` watches MLB; they are
  separate processes and neither knows about the other. The package and CLI are
  still called `cpbl_alert` / `cpbl-alert`, which is now half a lie — but a
  rename would break every existing invocation to fix a name nobody types twice.
- **MLB alerts are about people, not situations.** 心跳指數 is not applied
  there, so a Taiwanese hitter leading off a 10–0 game buzzes exactly as loudly
  as one batting with the bases loaded in the 9th. That is the feature: you
  asked to be told when he is up.
- **Restarting the MLB watcher re-announces whoever is on stage.** The tracker
  lives in memory, and "he is on the mound right now" is the present tense, so
  the first look at a game is deliberately not silent.
- **Chinese names for MLB players are a hand-kept table.** Anyone not in it
  shows up under the English name the API gave; the alert still fires.
- **No win-probability model.** 心跳指數 is a transparent heuristic built from
  public run-expectancy tables, not a trained CPBL win-probability model. It has
  no notion of who is pitching, who is due up, or bullpen state.
- **Run-expectancy tables are MLB-derived.** CPBL's run environment differs;
  only the relative ordering is used, which is stable, but the absolute numbers
  aren't CPBL-calibrated.
- **One-pitch lag**, as described above.
- **Regular season (`kindCode=A`) by default.** Postseason uses different codes.
- **Unofficial endpoints** — if CPBL changes its site, this breaks.
