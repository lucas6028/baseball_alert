# 快轉台

<p align="center">
  <img src="assets/readme-hero.png" alt="夜色球場中滿壘亮起，心跳訊號連向即時通知" width="100%">
</p>

> 有機會了，別錯過。

盯著中華職棒的實況，在比賽真正緊張的時候推一則 Telegram 或 Discord 給你——
九局滿壘、一分差的追平分上壘、八局撕破平手——大比分落後和無聊的半局則完全安靜。

也盯大聯盟和日職，但盯的是另一件事：**台灣選手上場**——鄧愷威登板、李灝宇站上
打擊區、古林睿煬在西武的主場踏上投手丘。同一支 bot、同樣四行。

打者的通知會**早一棒**送出。一個打席兩三分鐘就結束，等他站進打擊區才通知，開了
電視也只看到別人打擊；所以看的是「打擊區＋下一棒」兩格，他一進到這兩格就推。

投手沒有這個問題——中繼上來的當下就會被點名，那時他連熱身球都還沒投完，接著至少
要面對一名打者；先發更是好幾局的事。牛棚有沒有人在熱身兩邊都不公開，所以也沒有更
早的東西可以搬。能再早的只有**先發**：兩邊都在賽前就公告先發投手，所以多一則賽前
通知。

三個聯盟可以推到**不同的頻道**——中職進中職台，大聯盟、日職各進各的，
[往下看](#一個聯盟一個頻道)。

通知的標題就是產品名——Telegram 用聊天室名稱當標題，而那就是 bot 的顯示名稱
**快轉台**；Discord 則是 webhook 的名字。所以正文裡不再寫一次：那會用掉最稀有
的一行，去講一個螢幕上已經有的詞。

```
台鋼 4-5 富邦　九上・一出局
　◆　　打者 魔鷹
◆　◆　投手 曾峻岳
關鍵度 LI 7.82　平均=1.00
```

```
洋基 3-4 巨人　七上・兩出局
　◆　　打者 Judge
◇　◇　投手 鄧愷威
台灣投手登板　今日 1.2局・3K・失1
```

```
道奇 1-1 老虎　六下・無人出局
　◇　　打者 McGonigle
◇　◆　投手 Skubal
台灣打者下一棒 李灝宇　今日 0-2
```

```
西武 0-1 羅德　二上・兩出局
　◇　　打者 外崎
◇　◇　投手 高野脩
台灣打者下一棒 林安可　第七棒
```

下一棒那兩則，第二行寫的是**現在**站在打擊區的人——那是真的，也正是它告訴你還
有多少時間的方式。所以第四行改成把人名寫出來：他還不在上面兩行裡。

先發的賽前通知只有兩行，因為賽前也只有兩行是真的——比分、局數、壘包、打者都還不
存在。時間換算成台灣時間，因為那才是你會看的那個鐘。

```
洋基 @ 巨人　08:05 開賽
台灣投手先發 鄧愷威
```

## Quick start

Requires Python 3.10 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).

### Windows (PowerShell)

```bash
uv sync
uv run python init.py                       # configure + test notifications
uv run python -m cpbl_alert.cli all         # watch all three leagues
```

### Linux / macOS

```bash
uv sync
uv run python init.py                       # configure + test notifications
uv run python -m cpbl_alert.cli all         # watch all three leagues
```

The initializer prompts for Telegram, Discord, league-specific routing, and
alert preferences, writes `config.json`, then offers to send test messages.
Press Enter to keep an existing value when rerunning it; enter `-` to clear an
optional value. Secrets are hidden while you type. You can also launch the
same wizard with `uv run python -m cpbl_alert.cli init`.
The wizard uses color in interactive terminals, honors the `NO_COLOR`
convention, and emits plain text when its output is redirected.

The prompts include where to obtain every credential: Telegram bot tokens
come from [@BotFather](https://t.me/BotFather), `chat-id` discovers the chat ID
after you message the bot, and Discord webhook URLs come from **Server Settings
→ Integrations → Webhooks**.

If you use Telegram, send your new bot any message first. If you do not know
the chat id yet, finish setup without testing, run `chat-id`, then rerun the
initializer to enter the id. Prefer Discord? Leave the Telegram fields empty.
Set both, and every alert goes to both.
The three watchers are separate processes; run whichever of them you want to
follow.

### Getting a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Put the token in `config.json` (or set `TELEGRAM_TOKEN`).
3. Send your new bot any message.
4. Run `uv run python -m cpbl_alert.cli chat-id` and copy the id into `config.json`.

### Getting a Discord webhook

1. Server Settings → Integrations → Webhooks → **New Webhook**.
2. Pick the channel it posts to, and name it 快轉台 — that name is what
   Discord prints above every alert, which is why the four lines don't spend
   one saying it.
3. **Copy Webhook URL**, and paste it into `discord_webhook` in `config.json`
   (or set `DISCORD_WEBHOOK`).
4. `uv run python -m cpbl_alert.cli test` — the message should land in that channel.

The URL is a credential: anyone holding it can post to the channel. Keeping it
in the environment rather than in a file is why `DISCORD_WEBHOOK` exists.

### 一個聯盟，一個頻道

A webhook URL *is* a channel, so putting CPBL, MLB and NPB in different places
is a matter of making one webhook per channel and naming which is which. Any
channel key takes a `_cpbl`, `_mlb` or `_npb` suffix, and the suffixed one
wins for that league:

```json
{
  "discord_webhook_cpbl": "https://discord.com/api/webhooks/111.../aaa...",
  "discord_webhook_mlb":  "https://discord.com/api/webhooks/222.../bbb...",
  "discord_webhook_npb":  "https://discord.com/api/webhooks/333.../ccc..."
}
```

One webhook made in each channel: 中職台, 大聯盟台, 日職台.

You do not have to split all three. Leave a suffix out and that league falls
back to `discord_webhook`, so `discord_webhook` plus `discord_webhook_mlb`
puts 大聯盟 on its own and leaves 中職 and 日職 together — which is a real
setup, since those two barely overlap in the day. Leave all the suffixes out
and everything shares one channel. A league with no channel of its own and no
`discord_webhook` to fall back to prints to the console instead. Telegram
splits the same way with `telegram_chat_id_cpbl` / `_mlb` / `_npb`, so 中職
can go to a group chat while 日職 goes to a channel.

`test` sends once per *channel* rather than once per league — two leagues
sharing a webhook is one message, naming both — and each message says which
league it is testing, which is the thing worth checking once they are split:

```bash
uv run python -m cpbl_alert.cli test               # every channel configured
uv run python -m cpbl_alert.cli test --league npb  # just the NPB one
```

## Commands

| Command | What it does |
|---|---|
| `init` | Interactively create/update the configuration and send test notifications |
| `run` | Watch today's live games and push alerts. `--dry-run` prints instead, `--once` does a single pass |
| `all` | Watch all three leagues from one process, a thread each. `--league cpbl\|mlb\|npb` narrows it (repeat the flag for two); `--dry-run` and `--once` as above. Each league keeps its own poll rate and its own idle schedule, so a league with nothing on costs one request every five minutes |
| `live` | List today's games with real status (pending / LIVE / final) |
| `check <gameSno>` | Replay a real game through the model and show what *would* have fired — the way to tune your threshold |
| `test` | Send a test notification to every channel configured, one per channel. `--league cpbl\|mlb\|npb` tests just that league's; `--ruler` sends a numbered ruler instead, so you can see how many lines your phone shows before it truncates |
| `chat-id` | Look up your Telegram chat id |
| `mlb` | Watch MLB and push when a Taiwanese player takes the plate or the mound. `--dry-run` and `--once` as above |
| `mlb-live` | List the MLB games in the current window, with who is batting and pitching in each |
| `mlb-players` | List the Taiwanese players MLB currently has on its books, and where each Chinese name comes from |
| `npb` | Watch NPB and push when a Taiwanese player takes the plate or the mound. `--dry-run` and `--once` as above |
| `npb-live` | List today's NPB games (JST), with who is batting and pitching in each |
| `npb-players` | List the Taiwanese players NPB alerts fire for |
| `npb-probe` | Show every step of how a real npb.jp page becomes an alert — the header slice, the line score, both batting orders, the last event logged and who it says is up. **Run this once before trusting `npb`** — see [NPB](#npb) |

Tuning against a game you actually watched is the fastest way to find your
threshold:

```bash
uv run python -m cpbl_alert.cli check 290 --threshold 2.5
uv run python -m cpbl_alert.cli check 290 --all      # every pitch, with scores
```

## Configuration

`config.json` (environment variables win, which keeps tokens out of files).
Set `CPBL_ALERT_CONFIG` to use a config file at a different path:

| Key | Env | Default | Meaning |
|---|---|---|---|
| `telegram_token` | `TELEGRAM_TOKEN` | — | Bot token from BotFather |
| `telegram_chat_id` | `TELEGRAM_CHAT_ID` | — | Where to send |
| `telegram_chat_id_cpbl` | `TELEGRAM_CHAT_ID_CPBL` | — | CPBL's own chat, overriding the above for `run` |
| `telegram_chat_id_mlb` | `TELEGRAM_CHAT_ID_MLB` | — | MLB's own chat, overriding the above for `mlb` |
| `telegram_chat_id_npb` | `TELEGRAM_CHAT_ID_NPB` | — | NPB's own chat, overriding the above for `npb` |
| `discord_webhook` | `DISCORD_WEBHOOK` | — | Webhook URL; the URL *is* the channel |
| `discord_webhook_cpbl` | `DISCORD_WEBHOOK_CPBL` | — | CPBL's own channel, overriding the above for `run` |
| `discord_webhook_mlb` | `DISCORD_WEBHOOK_MLB` | — | MLB's own channel, overriding the above for `mlb` |
| `discord_webhook_npb` | `DISCORD_WEBHOOK_NPB` | — | NPB's own channel, overriding the above for `npb` |
| `threshold` | `CPBL_THRESHOLD` | `2.0` | Leverage Index that triggers an alert; `1.0` is average |
| `poll_seconds` | — | `15` | Seconds between polls (floored at 10) |
| `teams` | `CPBL_TEAMS` | `[]` | Only alert on these teams; empty means all |
| `mlb_players` | `MLB_PLAYERS` | `[]` | Extra MLB players to treat as Taiwanese, as ids or full names. Nationality otherwise comes from the API, so this is only for someone it does not record as Taiwan-born |
| `mlb_poll_seconds` | — | `20` | Seconds between MLB polls (floored at 10) |
| `npb_players` | `NPB_PLAYERS` | `[]` | Extra NPB players to alert on, by name in either orthography. npb.jp publishes no nationality, so unlike the MLB key this is the supported way to add a new signing rather than an escape hatch |
| `npb_poll_seconds` | — | `30` | Seconds between NPB polls (floored at 15) |

Configurations from the former 0–100 heartbeat scale are migrated
automatically: a legacy threshold such as `55` becomes the LI default `2.0`.

Telegram and Discord are independent: configure either, or both, and both get
the alert — one dead channel does not silence the other. Without credentials it
falls back to printing alerts to the console, so you can try it before setting
up a bot.

## How a situation is scored

Every pitch is assigned a **Leverage Index (LI)** for the plate appearance it
belongs to. LI measures the expected absolute movement in win probability from
the current game state and normalizes it against the league-wide average:

- `LI 1.00` is an average plate appearance.
- `LI 2.00` has twice the average potential impact and is the default alert
  threshold.
- `LI 7.82` is an extreme situation, such as one out and bases loaded in the
  ninth while trailing by one.

The lookup uses batting side, inning, outs, base occupancy and score
differential. It comes from Greg Stoll's open-source leverage model over
Retrosheet play-by-play, not from hand-selected inning or closeness
multipliers. The compact table is bundled in `cpbl_alert/li_table.py`, and
`scripts/build_li_table.py` reproduces the import from its upstream source.

The threshold is a notification preference, not part of the statistic. Raise
it for fewer interruptions; `check <gameSno> --all` prints the native LI for
every recorded pitch so it can be tuned against a game you watched.

### Alert volume

In the recorded 324-pitch game #290, 72 pitch rows meet the default `LI 2.00`
threshold. Rally deduplication collapses those repeated states to 12
notifications across the whole game.

Alerts fire on *changes*, not pitches. Within a half-inning you get one alert
when it crosses the threshold and another only if it materially escalates
(+1.00 LI). A momentary dip — an out mid-rally — does not re-arm it; only a
new half-inning does. Starting mid-game primes on existing pitches first, so
attaching in the 7th never replays the whole game at you. And only the
half-inning the game is actually in may reach your phone: a rally that both
started and ended between two polls is over, and telling you to turn the TV on
for it would be a lie.

## Data sources

### Leverage Index

The bundled LI table is derived from Retrosheet play-by-play by
[Greg Stoll's open-source baseballstats model](https://github.com/gregstoll/baseballstats/blob/master/processleveragefromcumulative.py).
It calculates the mean absolute win-expectancy swing across context-neutral
home-run, hit and out outcomes, then divides by the average swing across all
historical situations. That is what makes `1.00` the neutral baseline.

Only regulation innings and batting-team score differences from -8 through +8
are bundled. Wider margins return `0.00` so sparse historical outliers cannot
produce a false phone alert, and extra innings use the ninth-inning table.

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

### NPB

Same question as MLB — *is a Taiwanese player the one out there right now* —
and the same four lines on the phone, sharing the trigger in
`cpbl_alert/stage.py`. Almost nothing else is shared, because Japan gives you
none of the three things that made the MLB side easy.

- **There is no API.** npb.jp publishes HTML for people to read, so this side
  scrapes, and a tick costs the day's scoreboard plus one request per *live*
  game. That is why the default poll is 30s rather than 20s, and why a game the
  scoreboard already calls 試合終了 or 中止 is remembered and never fetched
  again — a finished game does not restart, and re-reading it every half minute
  until midnight is pure load on a site nobody is paying to serve it.
- **There is no nationality field.** `birthCountry` is what let the MLB module
  treat membership as a lookup and keep its name table as a mere backstop. Here
  the table *is* the detector, which inverts the failure mode: MLB's risk is a
  wrong Chinese name on a real alert, NPB's is no alert at all. Hence
  `npb_players` in the config — not an escape hatch so much as the supported way
  to add this year's signing without waiting for a release.
- **The names are written in Japanese.** 吳念庭 appears as 呉念庭 and 王彥程 as
  王彦程 — shinjitai forms of characters the player himself writes the
  traditional way. Comparing the string npb.jp prints against the string a
  Taiwanese reader would type therefore fails on precisely the players this tool
  exists for, so both sides go through a folding step first and the alert prints
  the traditional form back. The folding table holds only pairs that are
  unambiguously the same character; a wrong rule there costs a match.

Teams come off the letter code in the line score's own `flag_<code>_<year>`
class rather than off the printed name, for the same reason MLB teams come off
the id: a sponsor rename moves the name and leaves the code alone, which is
exactly what happened when 横浜ベイスターズ became 横浜DeNAベイスターズ. The
URL slug is only a fallback, and note that it names the **home** side first —
`f-h-21` is Hawks *at* Fighters, which is the opposite of what it looks like.

**npb.jp never says who is at the plate.** This is the one thing that makes
the NPB side different in kind from the other two. 最新経過 is a log of
*finished* plate appearances — no row ever appears without a result — and the
試合経過 tab is the same table. There is no next-batter panel, no count, no
runner display for the at-bat actually in progress.

So the batter is not read, he is derived: **the next man in 最新のオーダー
after the last one to finish**, matched by the person id both tables carry so
that a pinch hitter does not break it. One slot further on is the on-deck
alert. That derivation is checkable the only way it can be — does the man it
names turn out to be the man who finishes the *next* plate appearance logged?
Against a capture of six games through one live evening: 106 times out of 106.

The out count is carried across the play that just ended from the words in the
result (三振 is one, ショートゴロ併殺打 is two, フォアボール is none): 478
consecutive pairs of rows, right on all of them. The runners cannot always be
carried, because a scorecard line does not say how far a runner went — 64 of
71 transitions right, one wrong, six with no rule at all, and where there is no
rule the last published bases stand rather than an invented set. The diamond
is context; who is up is not guessed.

**The fixtures come off the month page too**, and off two different parts of
it. The *day rows* list every game of the month — both clubs, the ballpark,
the start time and, on the day, the 予告先発 — and carry no links at all. The
*header strip* is the six-game carousel on every npb.jp page, and it links a
game once it is under way. So the rows are how a game is known before it
starts and the strip is how it is found once it has; the two are paired by the
team code in the slug.

`npb-probe` prints every step of that: the day's fixtures and their 先発, what
survived the header slice, what the line score said, both batting orders, the
last event logged, what it carries forward, and who it concludes is up. An
empty `order` line or a `NO RULE` on the carry means the page has moved under
the rules; an empty `先発` before first pitch means the NPB starter notice will
stay quiet.

## Development

```bash
uv run python -m pytest tests/ -q        # 285 tests
uv run python scripts/replay.py --all    # offline, against the fixture
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

The NPB fixtures used to be hand-built, and they encoded a page that does not
exist — a `打者: 外崎` label beside a live count and a runner list. npb.jp
publishes none of that, and the parser written against the imagined page read
the word `カウント` as the batter's name. They are captures now: whole pages,
header carousel and all. `npb_live.html` is 西武 at ロッテ with two out in the
second, 外崎 at the plate and 林安可 — Taiwanese, batting seventh — on deck;
`npb_live_change_of_innings.html` is the same game with the side just retired
on a double play, which is where the leadoff man of the next half comes from;
`npb_final.html` is a game that has ended and publishes no log at all. Only
`npb_live_at_bat.html` is touched, and only by swapping one name in the order
table for 呉念庭 so that the at-the-plate path has an end-to-end fixture too —
real markup, planted name. Both live pages keep the six-game header strip,
which is what keeps the page-slicing honest.

## Known limits

- **Three leagues, three commands.** `run` watches CPBL, `mlb` watches MLB,
  `npb` watches NPB; they are separate processes and none knows about the
  others. The package and CLI are still called `cpbl_alert` / `cpbl-alert`,
  which is now two thirds of a lie — but a rename would break every existing
  invocation to fix a name nobody types twice. It is also why sending each
  league to its own channel is a config key rather than a router.
- **The NPB diamond is derived, and it is right about nine times in ten.**
  npb.jp publishes the situation a plate appearance *started* in, never the one
  in progress, so the runners have to be carried across the play that just
  ended — and a scorecard line does not say how far a runner went on a single.
  The out count and who is up are exact; the bases can be one play stale.
- **NPB alerts carry no stat line.** A batter's average and a pitcher's pitch
  count are on the 投打成績 tab, not the game page, so line four falls back to
  where he bats in the order. Fetching that tab when an alert fires — the way
  MLB fetches its boxscore — is the obvious next step.
- **NPB can lose a side for one at-bat.** 最新経過 keeps only the last two
  half-innings, so if a side's previous turn has scrolled off the page there is
  nothing to carry the order forward from, and that game stays quiet until one
  of its men finishes an at-bat. The ordinary change of innings is covered —
  the watcher rolls forward across it, which is exact: nobody on, nobody out,
  and the order carrying on.
- **An NPB game is found up to two minutes after it starts.** npb.jp will not
  serve a day index — `/scores/<year>/<mmdd>/` answers 403 to every client —
  so the fixtures come off the month page, which links a game only once it is
  under way. That page is 220KB, so it is read every two minutes rather than
  every poll, and not at all once every game of the evening has started.
- **NPB's 予告先発 may not be published before first pitch.** It is certainly
  on the month page once a game is under way; whether it is there beforehand
  could not be confirmed when this was written, and the whole NPB half of the
  starter notice depends on it. It fails as silence rather than as a wrong
  name — `npb-probe` prints the day's fixtures and their 先発, which settles
  it in one command on any morning.
- **A reliever gets no head start at all.** Nobody publishes a bullpen warming
  up, so on-the-mound is as early as a relief appearance can be known. It is
  early enough: he is named at the change, before his warmup pitches, and then
  faces at least one batter.
- **An NPB on-deck alert can be followed by a second one.** He is on deck with
  two out, the side is retired, and he leads off when his team bats again —
  two notifications about one trip to the plate. They are the right two: the
  first said he was next and the inning ended instead.
- **NPB membership is a hand-kept table, and it is the whole detector.** A
  player missing from it gets no alert at all, not merely a Japanese name on
  one. `npb_players` covers a new signing until the table catches up.
- **Alerts are about people, not situations.** LI is not applied
  in MLB or NPB, so a Taiwanese hitter leading off a 10–0 game buzzes exactly
  as loudly as one batting with the bases loaded in the 9th. That is the
  feature: you asked to be told when he is up.
- **Restarting either on-stage watcher re-announces whoever is on stage.** The tracker
  lives in memory, and "he is on the mound right now" is the present tense, so
  the first look at a game is deliberately not silent.
- **Chinese names for MLB players are a hand-kept table.** Anyone not in it
  shows up under the English name the API gave; the alert still fires.
- **LI is MLB-derived, not CPBL-calibrated.** The Retrosheet model is empirical
  and reproducible, but CPBL has a different run environment. It also has no
  notion of batter, pitcher, team strength, park or bullpen state. A future
  CPBL historical state table can replace the bundled values without changing
  the alerting interface.
- **Discord alerts are plain messages, not embeds.** The four lines were
  measured against a phone's lock screen and they read the same in a channel;
  an embed would buy colour and cost the shape. `<b>` becomes `**`, and that
  is the whole difference between what the two services are sent.
- **One-pitch lag**, as described above.
- **Regular season (`kindCode=A`) by default.** Postseason uses different codes.
- **Unofficial endpoints** — if CPBL changes its site, this breaks.
