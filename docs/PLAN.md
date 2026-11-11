# Aniimo 1v1 PvP — Ban/Pick Draft Tournament Bot (Plan)

A Python Discord bot that runs single-elimination Aniimo 1v1 tournaments with a
ban/pick draft. Everything happens inside Discord: no website, no frontend. The
bot runs on the organiser's own PC; it only makes outbound connections to
Discord, so no public URL, port forwarding or hosting is needed.

The "visual" part is a **draft board image** the bot renders with the real
Aniimo thumbnails and updates after every ban/pick, plus Aniimo icons in the
selection menus.

---

## 1. Decisions (agreed)

| Topic | Decision |
|---|---|
| Interface | Discord only: embeds, buttons, select menus, rendered PNG images |
| Hosting | Organiser's local PC (Windows/macOS/Linux), Python 3.11+ |
| Bracket | Single elimination, random shuffle seeding, automatic byes |
| Series length | Set per tournament by admin: Bo1 / Bo3 / Bo5 |
| Bans / picks | 2 bans + 4 picks per player |
| Draft order | Two-phase split (see §3) |
| First pick | Coin flip in game 1 → winner chooses side; later games → loser of previous game chooses |
| Mirrors | Not allowed: picks are exclusive |
| Timer | 45 s per step; timeout → ban skipped, pick = random from available |
| Match room | Private thread per match in `#matches` |
| Controls | Smart panel whose buttons change with the match stage |
| Results | One player reports → opponent confirms/disputes → auto-confirm after 10 min |
| No-show | Ping on open, reminder at 10 min, admin decision at 15 min |
| Pick input | Element menu → Aniimo menu (with icons) + 🔍 search-by-name |
| Roster | `data/roster.csv` + `assets/aniimo/*.png`, auto-synced as bot app emojis |
| Next round | Admin clicks **▶ Start Round N** after a round completes |
| Extras (v1) | Live draft feed, pick/ban stats, Fearless toggle, cross-event ELO leaderboard |
| Admin tools | Always included: undo step, override result, pause/resume, DQ, swap, extend |

---

## 2. Tournament flow

```
 #admin                         #announce                     #matches (private threads)
 ──────                         ─────────                     ──────────────────────────
 /tournament create  ──┐
 paste / attach CSV  ──┤ preview + [Confirm players]
 /tournament start   ──┼──────► 🎲 shuffle → bracket image ──► R1 threads created,
                       │        + match table                 players + admins added
                       │                                      │
                       │                                      ▼
                       │                               ready check → coin flip
                       │                               → draft → play in-game
                       │                               → report / confirm
                       │                                      │
 [▶ Start Round 2] ◄───┴──────── updated bracket ◄─── last R1 match confirmed
        │
        └─► R2 threads … → Final → 🏆 champion announcement + stats summary
```

### 2.1 Registration (admin channel)

1. `/tournament create name:"AniiCup #1" series:Bo3 fearless:false`
   (optional `final_series:Bo5` if the final should differ; defaults to `series`).
2. Admin pastes CSV text in `#admin` **or** attaches a `.csv` file
   (or uses `/tournament players file:<csv>`):

   ```csv
   discord_id,aniimo_uid,display_name
   123456789012345678,100234567,Luna
   234567890123456789,100345678,Kai
   ```

   * Header row optional; `display_name` optional (falls back to server nickname).
   * Validation: Discord ID is a member of the server, no duplicate IDs/UIDs,
     UID not empty. Errors are listed per row.
3. Bot replies with a preview embed (player count, bracket size, number of byes,
   errors) and **[✅ Confirm players] [✖ Cancel]**. Re-posting a CSV replaces the
   list until the tournament starts.

### 2.2 Shuffle + announce

* `/tournament start` (or button) → players are shuffled (`secrets`-based RNG,
  seed logged for audit) and placed into a bracket of size `2^k`.
* Byes are spread using standard seed positions, so a bye never meets a bye;
  bye matches resolve instantly.
* `#announce` gets: a rendered **bracket image** + a text table of Round 1
  matches (with mentions), so it's readable on phone too.

### 2.3 Match rooms

* One **private thread** per match: `R1 · M3 · Luna vs Kai`, in `#matches`.
  Both players and the admin role are added; thread auto-archives after the
  match.
* First message: both players' **Aniimo UIDs** in copyable code blocks and who
  hosts the in-game room (player 1 creates the room and invites player 2 by UID).
* Below it: the **match control panel** (§4).

### 2.4 Round progression

* When the last match of a round is confirmed: the bot posts the updated bracket
  image in `#announce` and sends admins **[▶ Start Round N]**.
* Clicking it creates the next round's threads.
* After the final: champion announcement, final bracket, tournament stats
  summary (most banned/picked, MVP Aniimo), ELO updates.

---

## 3. Draft rules

### 3.1 Order: two-phase split (12 steps)

```
PHASE 1 — BAN    A ban  → B ban
PHASE 1 — PICK   A pick → B pick → B pick → A pick
PHASE 2 — BAN    B ban  → A ban
PHASE 2 — PICK   B pick → A pick → A pick → B pick
```

* **A** = side with first ban + first pick; **B** = side with 2nd-phase first
  ban + last pick (counter-pick).
* A picks at positions #1 #4 #6 #7, B at #2 #3 #5 #8.
* The order lives in one config list, so it can be changed without touching code.

### 3.2 Side selection

* **Game 1:** bot flips a coin → winner gets
  **[Take side A (first pick)] [Take side B (counter-pick)]**.
* **Game 2+:** loser of the previous game chooses.
* No answer within 2 min → the chooser gets side A by default.

### 3.3 Rules

* Bans remove an Aniimo for both players. Picks are exclusive: once picked,
  gone for both.
* **Fearless (per-tournament toggle):** in a series, an Aniimo you picked in an
  earlier game of that series can't be picked by you again (your opponent still
  can). Bans reset every game.
* **Timer:** 45 s per step, shown as a live Discord countdown
  (`<t:…:R>`, so no message spam). Timeout → ban skipped / random legal pick,
  marked "⏱ auto" on the board.
* Each game gets a fresh draft.

### 3.4 Picking UX (per step)

1. Thread message: "🎯 **Kai — your PICK** (ends in 38s)" + board image +
   **[🎯 Make your choice]**. Only the active player can use it; anyone else gets
   a private "not your turn" note.
2. The button opens a **private (ephemeral) picker** for that player only:
   `[Element ▾]` → `[Aniimo ▾ (icons)]` → **[🔒 Lock in]** · **[🔍 Search by name]**.
   Banned/picked/fearless-locked Aniimo are hidden.
3. On lock-in: board image updates in the thread and in `#live-draft`, next
   step starts.

### 3.5 Draft board image (1280×720 PNG, Pillow)

```
┌──────────────────────────────────────────────────────────────────────┐
│ AniiCup #1 · Round 1 · Match 3 · Game 2           Series  Luna 1–0 Kai│
├───────────────────────┬────────────────────┬─────────────────────────┤
│ LUNA  (A)             │   PHASE 2 · PICK   │              KAI  (B)   │
│ BANS  [✕img][✕img]    │  ●●●●●●●●○○○○      │    [✕img][✕img]  BANS   │
│                       │                    │                         │
│ [ img ][ img ]        │   ▶ KAI picking    │        [ img ][ img ]   │
│ [ img ][ ··· ]        │                    │        [ ··· ][ ··· ]   │
│ name · element icon   │                    │   name · element icon   │
└───────────────────────┴────────────────────┴─────────────────────────┘
```

* Blue/red player sides, active slot highlighted, banned thumbnails greyed
  with red ✕, auto-picks tagged.
* Missing thumbnail → generated placeholder (element colour + initials), so
  the bot works before all images are added.

---

## 4. Match control panel (smart panel)

One message per match; its buttons change with the stage.

| Stage | Panel shows | Notes |
|---|---|---|
| 1. Waiting | `Luna ⬜ Kai ⬜` **[✅ Ready] [🆘 Call admin]** | Both ready → stage 2. No-show timer runs (§4.1). |
| 2. Side choice | 🪙 coin result + **[Side A] [Side B]** | Only the chooser can click. |
| 3. Drafting | Board + **[🎯 Make your choice]** | §3.4 |
| 4. Playing | Final board + **[🏆 Report result] [🔁 Rematch] [🆘 Call admin]** | Players play in-game. |
| 5. Confirming | "Luna reported: Luna won G2" **[✅ Confirm] [⚠️ Dispute]** (opponent only) | Auto-confirm after 10 min. |
| 6. Between games | Score `1–0` **[▶ Next game] [🆘 Call admin]** | Loser chooses side → stage 3. |
| 7. Done | Winner, series score, all boards | Winner advances; thread archived. |

* **Report result:** pick winner (two buttons), optional screenshot attachment.
* **Rematch:** asks **[Replay same teams]** (disconnect/bug: draft kept) or
  **[New draft]** (redo this game's draft). The opponent must accept; admins
  can force it. Doesn't change the series score.
* **Dispute / Call admin:** pings the admin role in the thread with the
  current state + admin buttons.

### 4.1 No-show

* Players pinged when the room opens → reminder at 10 min → at 15 min admins
  get **[Give win to X] [Extend 10 min] [DQ both]**.

---

## 5. Admin tools

Slash commands restricted to the configured admin role:

| Command | Effect |
|---|---|
| `/admin undo match:<id>` | Undo the last draft step |
| `/admin pause` / `/admin resume match:<id>` | Freeze/unfreeze draft timer |
| `/admin set-result match:<id> winner:<@user> score:2-1` | Override a game/match result |
| `/admin replay match:<id> mode:same-teams\|new-draft` | Force rematch |
| `/admin dq player:<@user>` | Disqualify → opponent advances |
| `/admin swap old:<@user> new:<@user> uid:<uid>` | Replace a player before they've played |
| `/admin extend match:<id> minutes:10` | Extend no-show timer |
| `/tournament status` / `/tournament cancel` | Overview / cancel |
| `/roster reload` | Re-read `roster.csv` + images, re-sync emojis |

Every admin action is written to an audit log and echoed in the match thread.

---

## 6. Extras (v1)

* **Live draft feed:** one message per active match in `#live-draft`, edited at
  every step (no spam); final board kept as a record.
* **Stats:** `/stats aniimo` (pick %, ban %, win rate, presence),
  `/stats player @user` (history, favourite Aniimo, most-banned-against),
  per-tournament summary posted after the final.
* **Fearless series:** per-tournament toggle (§3.3).
* **Leaderboard:** ELO (start 1000, K=32, per match) across tournaments;
  `/leaderboard`, `/profile @user` (rating, W/L, titles, top Aniimo).

---

## 7. Roster & thumbnails

```
data/roster.csv
  slug,name,element,role,image
  embertail,Embertail,Fire,Attacker,embertail.png
assets/aniimo/embertail.png        # square PNG, ≥128×128 recommended
assets/elements/fire.png           # optional element icons
```

* On startup (and `/roster reload`) the bot validates the file and uploads each
  thumbnail as an **application emoji** (bots can own up to 2000, no server
  boost needed). It only uploads new or changed images (content hash).
* Menus show the emoji icon; board images use the full PNG.
* A template `roster.csv` ships with the column layout and a few placeholder
  rows. The organiser fills in the real roster and drops in thumbnails.
  Official Aniimo art belongs to its publisher, so the images aren't committed
  to the repo (`assets/aniimo/` is git-ignored).

---

## 8. Technical design

### 8.1 Stack

* Python 3.11+, **discord.py 2.6** (slash commands, persistent views, app emojis)
* **Pillow** for board + bracket images (bundled OFL font)
* **SQLite** via SQLAlchemy 2 (async, aiosqlite): one file, zero setup
* `pydantic-settings` for config, `pytest` + `pytest-asyncio` for tests

### 8.2 Layout

```
aniimo-pvp/
├─ pyproject.toml
├─ .env.example              # DISCORD_TOKEN, GUILD_ID
├─ config.example.yaml       # channel/role IDs, timers, draft order
├─ data/roster.csv
├─ assets/{aniimo,elements,fonts}/
├─ src/aniimo_pvp/
│  ├─ __main__.py            # python -m aniimo_pvp
│  ├─ bot.py  config.py
│  ├─ domain/                # pure logic, no Discord: fully unit-tested
│  │  ├─ draft.py            # state machine, legality, fearless, timeouts
│  │  ├─ bracket.py          # shuffle, byes, advancement
│  │  ├─ series.py           # BoN scoring, side-choice rules
│  │  ├─ elo.py  roster.py  csv_import.py
│  ├─ db/  models.py  repo.py
│  ├─ render/  board.py  bracket.py  placeholder.py
│  ├─ discord_ui/            # views: panel, picker, confirm, admin
│  └─ cogs/  tournament.py  match.py  draft.py  admin.py  stats.py  roster.py
└─ tests/
```

### 8.3 Data model (SQLite)

* `players(discord_id PK, aniimo_uid, display_name, rating, wins, losses)`
* `tournaments(id, guild_id, name, status, series_bo, final_bo, fearless, rng_seed, created_by, created_at)`
* `entries(tournament_id, discord_id, seed, status)`
* `matches(id, tournament_id, round, slot, p1_id, p2_id, winner_id, status, thread_id, panel_msg_id, live_msg_id, next_match_id, next_slot, opened_at)`
* `games(id, match_id, number, side_a_id, winner_id, status, reported_by, confirmed_at, screenshot_url, replay_of)`
* `draft_actions(id, game_id, step, player_id, kind[ban|pick|skip], aniimo_slug, auto, created_at)`
* `audit_log(id, tournament_id, actor_id, action, payload_json, created_at)`

### 8.4 Reliability (runs on a home PC)

* All state lives in SQLite; nothing important is kept only in memory.
* Buttons are **persistent views** (custom IDs encode match/game IDs), so
  they keep working after a bot restart.
* On startup the bot resumes open drafts. A timer that expired while offline
  restarts at a full 45 s instead of auto-picking.
* Every state change goes through `domain/` functions that validate the
  transition (e.g. you can't report while drafting, or pick out of turn), and
  each match is protected by an asyncio lock against double-clicks.

### 8.5 Discord setup (README will walk through it)

* Create the app in the Developer Portal → bot token into `.env`.
* Intents: Guilds, Guild Messages, **Message Content** (to read pasted CSV in
  `#admin`; a toggle only, no verification for small bots).
* Permissions: View Channels, Send Messages, Send Messages in Threads,
  Create Private Threads, Manage Threads, Embed Links, Attach Files,
  Read Message History, Mention roles.
* Config: `#admin`, `#announce`, `#matches`, `#live-draft` channel IDs and
  the admin role ID.

---

## 9. Build phases

| # | Phase | Deliverable |
|---|---|---|
| 1 | Foundation | Project skeleton, config, DB, roster loader + emoji sync, CSV import + preview |
| 2 | Tournament & bracket | create/players/start, shuffle + byes, bracket image, announce table, R1 threads |
| 3 | Match panel | Ready check, no-show timer, coin flip + side choice, UID card |
| 4 | Draft | Draft engine, ephemeral picker, board renderer, 45 s timer + auto actions |
| 5 | Results & progression | Report/confirm/dispute, BoN series, rematch, Start Round N, champion |
| 6 | Admin tools | Commands in §5 + audit log |
| 7 | Extras | Live feed, stats, Fearless, ELO leaderboard |
| 8 | Polish | README setup guide, sample roster, test suite, run scripts (Win/macOS/Linux) |

Testing: the `domain/` layer (draft order, legality, fearless, timeouts,
bracket/byes, series scoring, ELO, CSV parsing) gets unit tests. Discord flows
are tested manually on a test server with two accounts.

---

## 10. Smaller defaults chosen (change any time)

* CSV columns `discord_id,aniimo_uid[,display_name]`, header optional.
* Player 1 (top of bracket slot) creates the in-game room.
* Side choice timeout 2 min → chooser gets side A.
* Result auto-confirm after 10 min.
* Fearless is per-player ("you can't re-pick your own"), bans reset each game.
* ELO per match, K=32, start 1000.
