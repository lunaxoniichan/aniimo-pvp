# System Design — Aniimo 1v1 Draft Tournament Bot

A Discord-only bot, written in Python and run on the organiser's PC, that runs
single-elimination Aniimo 1v1 tournaments with a ban/pick draft. Players never
leave Discord. The visual part is a draft-board image rendered with real Aniimo
thumbnails.

## 1. Decisions

| Area | Decision |
|---|---|
| Interface | Discord only: slash commands, embeds, buttons, select menus, rendered PNGs |
| Hosting | Organiser's PC, Python 3.11+. Outbound gateway connection only, so no public URL |
| Bracket | Single elimination, random shuffle, byes spread so no bye meets a bye |
| Series | Bo1 / Bo3 / Bo5 per tournament (optional different final) |
| Draft | 2 bans + 4 picks per player, two-phase split order, exclusive picks |
| Side choice | G1 coin flip, then winner chooses · G2+ previous game's loser chooses |
| Timer | 45 s/step; timeout → ban skipped, pick random |
| Match room | Private thread per match in `#matches` |
| Results | Report → opponent confirms/disputes → auto-confirm after 10 min |
| No-show | Reminder at 10 min, admin decides at 15 min |
| Next round | Admin clicks **▶ Start Round N** |
| Extras | Live draft feed, pick/ban stats, Fearless toggle, ELO leaderboard |

## 2. Architecture

```mermaid
flowchart LR
  subgraph Discord["Discord (cloud)"]
    U1(["👤 Players"])
    U2(["🛡️ Admins"])
    CH["admin · announce · live-draft channels<br/>private match threads"]
  end
  subgraph PC["Organiser PC · python -m aniimo_pvp"]
    COGS["cogs + discord_ui<br/>commands · buttons · menus"]
    SVC["services<br/>tournament · match · draft · scheduler"]
    DOM["domain (pure Python)<br/>draft · bracket · series · elo · csv"]
    REN["render (Pillow)<br/>board · bracket images"]
    DB[("SQLite<br/>data/aniimo.db")]
    FILES[/"roster.csv<br/>assets/aniimo/*.png"/]
  end
  U1 & U2 <--> CH
  CH <-->|"gateway websocket<br/>outbound only"| COGS
  COGS --> SVC
  SVC --> DOM
  SVC --> REN
  SVC <--> DB
  FILES --> SVC
  FILES --> REN
```

| Layer | Responsibility | Discord-aware? |
|---|---|---|
| `cogs/`, `discord_ui/` | Commands, views, permission checks, message edits | yes |
| `services/` | Orchestrate a use case: load state → call domain → persist → notify | via interfaces |
| `domain/` | Rules: draft order and legality, bracket and byes, series scoring, Fearless, ELO, CSV parsing | no, unit-tested |
| `render/` | Board and bracket PNGs, placeholder thumbnails | no |
| `db/` | SQLAlchemy models + repository | no |

## 3. User flows

### 3.1 Admin

```mermaid
flowchart TD
  A["Run /tournament create<br/>name · series · fearless"] --> B["Paste or attach CSV in admin channel"]
  B --> C{"Preview OK?"}
  C -- "errors listed" --> B
  C -- "✅ Confirm players" --> D["Run /tournament start"]
  D --> E["Bot shuffles · posts bracket + R1 table<br/>opens R1 match threads"]
  E --> F["Monitor: disputes · no-shows · Call admin"]
  F --> G{"Round complete?"}
  G -- "no" --> F
  G -- "yes" --> H["Click ▶ Start Round N"] --> F
  G -- "final done" --> I(["🏆 Champion · stats · ELO posted"])
```

### 3.2 Player

```mermaid
flowchart TD
  P0(["Pinged: match room open"]) --> P1["Open private thread<br/>copy opponent's Aniimo UID"]
  P1 --> P2["Add each other in-game<br/>player 1 hosts the room"]
  P2 --> P3["Click ✅ Ready"]
  P3 --> P4{"Who picks the side?<br/>G1 coin flip · G2+ previous loser"}
  P4 --> P5["Chooser takes Side A or Side B"]
  P5 --> P6["Draft · 12 steps · 45 s each<br/>🎯 Make your choice opens a private picker"]
  P6 --> P7["Play the game in Aniimo"]
  P7 --> P8["🏆 Report result · opponent confirms"]
  P8 --> P9{"Series decided?"}
  P9 -- "no" --> P10["▶ Next game"] --> P4
  P9 -- "won" --> P11(["Advance · wait for next round"])
  P9 -- "lost" --> P12(["Eliminated · stats + rating updated"])
  P7 -. "disconnect / bug" .-> P13["🔁 Rematch · opponent accepts"]
  P13 -- "same teams" --> P7
  P13 -- "new draft" --> P6
```

### 3.3 Registration → Round 1

```mermaid
sequenceDiagram
  actor Admin
  participant Bot
  participant AdminCh as Admin channel
  participant Ann as Announce channel
  participant Th as Match threads
  Admin->>Bot: /tournament create (name, series, fearless)
  Bot-->>Admin: Created · status REGISTRATION
  Admin->>AdminCh: Paste CSV or attach .csv
  Bot->>Bot: Parse + validate (member? duplicate? UID?)
  Bot-->>AdminCh: Preview: N players, bracket size, byes, errors
  Admin->>Bot: ✅ Confirm players
  Admin->>Bot: /tournament start
  Bot->>Bot: Shuffle (logged seed) · build bracket · resolve byes
  Bot->>Ann: Bracket image + Round 1 table with mentions
  loop each Round 1 match
    Bot->>Th: Create private thread · add players + admins
    Bot->>Th: UID card + control panel · ping players
  end
```

## 4. Match lifecycle

One **control panel** message per match; its buttons always match the current state.

```mermaid
stateDiagram-v2
  state "Game decided" as Decided
  [*] --> Waiting : room opened
  Waiting --> SideChoice : both Ready
  Waiting --> NoShow : 15 min
  NoShow --> Waiting : admin extends 10 min
  NoShow --> Done : admin gives win or DQ
  SideChoice --> Drafting : side chosen or 2 min timeout
  Drafting --> Paused : admin pause
  Paused --> Drafting : admin resume
  Drafting --> Playing : step 12 locked
  Playing --> Playing : Rematch same teams (same game, attempt +1)
  Playing --> Drafting : Rematch new draft (same game, attempt +1)
  Playing --> Confirming : Report result
  Confirming --> Disputed : opponent disputes
  Confirming --> Decided : confirmed or 10 min
  Disputed --> Decided : admin set-result
  Disputed --> Playing : admin replay (attempt +1)
  Decided --> BetweenGames : series open (Bo3 or Bo5 only)
  Decided --> Done : series won (Bo1 always)
  BetweenGames --> SideChoice : Next game (game +1)
  Done --> [*] : winner advances
```

| State | Panel buttons | Who can click |
|---|---|---|
| Waiting | ✅ Ready · 🆘 Call admin | both players |
| SideChoice | Side A (first pick) · Side B (counter-pick) | chooser |
| Drafting | 🎯 Make your choice | active player (others get a private "not your turn") |
| Playing | 🏆 Report result · 🔁 Rematch · 🆘 Call admin | both players |
| Confirming | ✅ Confirm · ⚠️ Dispute | opponent of reporter |
| BetweenGames (Bo3/Bo5 only) | ▶ Next game · 🆘 Call admin | both players |
| NoShow / Disputed | Give win to X · Extend · DQ both / set-result | admin role |

### 4.1 Rematch vs Next game

The two buttons are separate actions and write different rows, so the series
score can always be recomputed from the database.

| | 🔁 Rematch | ▶ Next game |
|---|---|---|
| Meaning | Replay the **current** game (disconnect, bug) | Start the **next** game of the series |
| `GAME` row | Same `number`, `attempt + 1`; the old attempt becomes `voided` | `number + 1`, `attempt = 1` |
| Draft | Same teams → copied (`draft_source_id`) · New draft → restarts at step 1 | Fresh draft |
| Score | Unchanged | Unchanged until this game is confirmed |
| When | While `Playing` (before a result is reported); opponent accepts or admin forces | After the previous game is `confirmed` and nobody has the wins needed |
| Series length | Bo1, Bo3, Bo5 | **Bo3 and Bo5 only**, never in Bo1 |
| Side | Same sides as the voided attempt | Loser of the previous confirmed game chooses |

Example, Bo3, with one rematch:

| Game | Attempt | Status | Winner | Draft | Score |
|---|---|---|---|---|---|
| 1 | 1 | confirmed | Luna | new | 1–0 |
| 2 | 1 | voided (rematch, same teams) | — | new | 1–0 |
| 2 | 2 | confirmed | Kai | copied from attempt 1 | 1–1 |
| 3 | 1 | confirmed | Luna | new | 2–1, match over |

* **Score** = count of `confirmed` games per player. Wins needed =
  ⌈Bo / 2⌉ (Bo1 → 1, Bo3 → 2, Bo5 → 3).
* **Voided attempts** never count toward the score, ELO, Fearless or pick/ban stats.
* A result that's already reported can't be rematched by players. They
  dispute it, and the admin either sets the result or runs `/admin replay`.

## 5. Draft

### 5.1 Order: two-phase split

```mermaid
flowchart TB
  subgraph B1["Phase 1 · Ban"]
    direction LR
    s1["1 · A ban"] --> s2["2 · B ban"]
  end
  subgraph K1["Phase 1 · Pick"]
    direction LR
    s3["3 · A pick"] --> s4["4 · B pick"] --> s5["5 · B pick"] --> s6["6 · A pick"]
  end
  subgraph B2["Phase 2 · Ban"]
    direction LR
    s7["7 · B ban"] --> s8["8 · A ban"]
  end
  subgraph K2["Phase 2 · Pick"]
    direction LR
    s9["9 · B pick"] --> s10["10 · A pick"] --> s11["11 · A pick"] --> s12["12 · B pick"]
  end
  B1 --> K1 --> B2 --> K2
  classDef sideA fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
  classDef sideB fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
  class s1,s3,s6,s8,s10,s11 sideA
  class s2,s4,s5,s7,s9,s12 sideB
```

* **A** (blue) gets the first ban and first pick. **B** (red) gets the first
  ban of phase 2 and the last pick (counter-pick).
* The order is a config list, `[(side, kind), …]`, so it can be changed without code changes.
* **Legal choice** = in roster, not banned, not picked by anyone in this game,
  and (if Fearless) not picked by this player in an earlier **confirmed** game
  of the series.

### 5.2 One turn

```mermaid
sequenceDiagram
  actor P as Active player
  actor O as Opponent
  participant Bot
  participant T as Match thread
  participant L as Live-draft channel
  Bot->>T: Board image · "Kai — PICK" · countdown · 🎯 button
  O->>Bot: Clicks 🎯
  Bot-->>O: Private: not your turn
  P->>Bot: Clicks 🎯
  Bot-->>P: Private picker: element menu
  P->>Bot: Chooses element
  Bot-->>P: Aniimo menu (legal only, with icons) · 🔍 search
  P->>Bot: Chooses Aniimo · 🔒 Lock in
  Bot->>Bot: Lock match · check turn + step id + legality · save · cancel timer
  Bot->>T: Edit board · next turn
  Bot->>L: Edit live board
  alt 45 s timeout
    Bot->>Bot: Ban → skip · Pick → random legal (tagged auto)
    Bot->>T: Edit board · next turn
  end
```

### 5.3 Board image (1280×720, Pillow)

```
┌────────────────────────────────────────────────────────────────────┐
│ AniiCup #1 · R1 · M3 · Game 2                  Series  Luna 1–0 Kai│
├──────────────────────┬──────────────────────┬──────────────────────┤
│ LUNA (A)  🟦          │   PHASE 2 · PICK     │          🟥  KAI (B)  │
│ BANS [✕][✕]           │   ●●●●●●●●○○○○       │           [✕][✕] BANS │
│ [img][img][img][ ··· ]│   ▶ KAI picking      │ [img][img][ ··· ][···]│
└──────────────────────┴──────────────────────┴──────────────────────┘
```

Bans are greyed with a red ✕, the active slot glows, and auto actions are tagged.
A missing thumbnail falls back to a placeholder in the element colour with the
creature's initials.

## 6. Results and advancement

```mermaid
sequenceDiagram
  actor R as Reporter
  actor O as Opponent
  participant Bot
  participant T as Match thread
  participant Ann as Announce channel
  actor Adm as Admin role
  R->>Bot: 🏆 Report result (winner, optional screenshot)
  Bot->>T: "Luna reported: Luna won G1" · Confirm / Dispute
  alt Opponent confirms or 10 min pass
    Bot->>Bot: Record game · update series score
  else Opponent disputes
    Bot->>T: Ping admin role with state
    Adm->>Bot: /admin set-result or /admin replay
  end
  alt Series won
    Bot->>T: Winner card · archive thread
    Bot->>Bot: Advance winner · update ELO
    opt Last match of the round
      Bot->>Ann: Updated bracket image
      Bot->>Adm: ▶ Start Round N
    end
  else Series continues (Bo3 or Bo5 only)
    Bot->>T: Score + ▶ Next game (loser chooses side)
  end
```

## 7. Data model (SQLite)

```mermaid
erDiagram
  PLAYER ||--o{ ENTRY : joins
  TOURNAMENT ||--o{ ENTRY : has
  TOURNAMENT ||--o{ MATCH : contains
  MATCH ||--|{ GAME : "series of"
  GAME ||--o{ DRAFT_ACTION : records
  MATCH |o--o| MATCH : "winner feeds"
  TOURNAMENT ||--o{ AUDIT_LOG : logs
  PLAYER {
    bigint discord_id PK
    string aniimo_uid UK
    string display_name
    int rating
    int wins
    int losses
  }
  TOURNAMENT {
    int id PK
    bigint guild_id
    string name
    string status "registration, running, finished, cancelled"
    int series_bo
    int final_bo
    bool fearless
    string rng_seed
  }
  ENTRY {
    int tournament_id FK
    bigint discord_id FK
    int seed
    string status "active, eliminated, dq"
  }
  MATCH {
    int id PK
    int tournament_id FK
    int round
    int slot
    bigint p1_id FK
    bigint p2_id FK
    bigint winner_id FK
    string state
    bigint thread_id
    bigint panel_msg_id
    bigint live_msg_id
    int next_match_id FK
  }
  GAME {
    int id PK
    int match_id FK
    int number "1 to Bo"
    int attempt "1, +1 per rematch"
    bigint side_a_id FK
    bigint winner_id FK
    string status "drafting, playing, reported, confirmed, disputed, voided"
    string void_reason "rematch_same_teams, rematch_new_draft, admin_replay"
    int draft_source_id FK "set when teams are copied"
    bigint reported_by
    string screenshot_url
  }
  DRAFT_ACTION {
    int id PK
    int game_id FK
    int step
    bigint player_id FK
    string kind "ban, pick, skip"
    string aniimo_slug
    bool auto
  }
  JOB {
    int id PK
    string kind
    int ref_id
    datetime due_at
    string status
  }
  AUDIT_LOG {
    int id PK
    int tournament_id FK
    bigint actor_id
    string action
    json payload
    datetime created_at
  }
```

The roster (Aniimo name, element, role, image) lives in `data/roster.csv`, not
the database. Stats join on `aniimo_slug`.

`GAME` is unique on `(match_id, number, attempt)`, and at most one attempt per
`number` is not `voided`. The series score, ELO, Fearless and stats read
`confirmed` games only (§4.1).

## 8. Timers and restart safety

All timers are rows in `JOB`, run by one asyncio scheduler, so they survive a restart.

| Job | Due after | On fire |
|---|---|---|
| `draft_step` | 45 s | ban → skip · pick → random legal |
| `side_choice` | 2 min | chooser gets Side A |
| `noshow_reminder` | 10 min | ping players who aren't ready |
| `noshow_escalate` | 15 min | admin buttons: Give win · Extend · DQ both |
| `auto_confirm` | 10 min | reported result accepted |
| `rematch_offer` | 2 min | offer expires |

* **Restart rule:** a job that came due while the bot was offline is restarted
  with its full duration, because players couldn't click anything during the downtime.
* **Persistent buttons:** custom IDs look like `ap:<action>:<match>:<game>:<step>`
  (discord.py `DynamicItem`), so buttons keep working after a restart.
* **Stale or double clicks:** a per-match `asyncio.Lock` plus the step ID in the
  custom ID. An old button replies "this turn is over" and changes nothing.

## 9. Discord surface

| Channel | Content |
|---|---|
| `#admin` | CSV paste, previews, admin alerts, Start Round N |
| `#announce` | Bracket image + round tables, champion post |
| `#matches` | Private thread per match: UID card, panel, board |
| `#live-draft` | One live board per active match, edited each step |

| Command | Who |
|---|---|
| `/tournament create · players · start · status · cancel` | admin |
| `/admin undo · pause · resume · set-result · replay · dq · swap · extend` | admin |
| `/roster reload` | admin |
| `/stats aniimo · /stats player · /leaderboard · /profile` | everyone |

Bot setup: Message Content intent (to read pasted CSV). Permissions: View
Channels, Send Messages, Send in Threads, Create Private Threads, Manage
Threads, Embed Links, Attach Files, Read History, Mention roles.

## 10. Roster and thumbnails

* `data/roster.csv`: `slug,name,element,role,image`. Images go in
  `assets/aniimo/` (git-ignored, because the art belongs to the publisher).
* On startup and `/roster reload`, the bot validates the file and uploads new or
  changed images as **application emojis** (up to 2000 per bot, no server boost).
  It compares content hashes to skip unchanged images.
* Menus show the emoji icon and board images use the full PNG.
