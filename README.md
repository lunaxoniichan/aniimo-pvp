# aniimo-pvp

Unofficial ban/pick draft mode for **Aniimo 1v1 PvP**, run as a Discord bot. The bot runs on
your own PC; there is no website. Players draft, play and report inside Discord. The bot keeps
the bracket and posts a draft-board image with the Aniimo thumbnails after every ban and pick.

- **Tournaments**: single elimination for up to 128 players, random shuffle, automatic byes.
- **Formats**: Quick, Standard or Championship sets the Bo1/Bo3/Bo5 for each round.
- **Draft**: 2 bans + 4 picks each, in a two-phase split order. Picks are exclusive. 45 s per step.
- **Match rooms**: a private thread per match with the players' Aniimo UIDs, a ready check,
  a coin flip, Report / Confirm / Dispute, Rematch, and Next game (Bo3/Bo5).
- **Admin tools**: undo, pause, set result, award, replay, disqualify, swap and extend.
- **Extras**: a live draft feed, pick/ban stats, an optional Fearless mode, and a rating leaderboard.

Design docs: [system design](docs/SYSTEM_DESIGN.md) · [implementation plan](docs/IMPLEMENTATION.md) ·
[approved mockups](https://claude.ai/artifact/7112nacYyipqnhKMCC3ELu)

---

## Quick start

1. Install **Python 3.11+** (Windows: from python.org, tick "Add to PATH").
2. Do the [Discord setup](#discord-setup) below, which takes about 10 minutes.
3. Run `run.bat` (Windows) or `./run.sh` (macOS/Linux). The first run creates `config.yaml`,
   `.env` and `data/roster.csv`, then stops.
4. Put your bot token in `.env`, fill in the IDs in `config.yaml`, and replace the example roster
   (see [Roster and thumbnails](#roster-and-thumbnails)).
5. Run `run.bat check` / `./run.sh check` until everything is ✓, then `run.bat` / `./run.sh` to start.

The bot only makes outbound connections to Discord, so you don't need port forwarding or a
public address. Keep the PC running during the tournament.

## Discord setup

1. Open <https://discord.com/developers/applications> → **New Application** (e.g. "Aniimo Draft").
2. **Bot** page:
   - **Reset Token** → copy it into `.env` as `DISCORD_TOKEN=...` (keep it secret).
   - Turn on **Message Content Intent**, so the bot can read the player CSV you paste.
3. **Invite the bot**. Replace `YOUR_APP_ID` with the Application ID from the General Information page:

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=395137502272
   ```

   That grants View Channels, Send Messages, Send Messages in Threads, Create Private/Public Threads,
   Manage Threads, Embed Links, Attach Files, Read Message History, Add Reactions, Use External Emojis
   and Mention All Roles.
4. In your server, create:
   - a **role** for tournament admins, e.g. `Tournament Admin`. Give it **Manage Threads** in the
     matches channel so admins can see every private match thread.
   - `#admin`, visible to admins only: the setup panel, CSV paste and alerts.
   - `#announce`: brackets and results.
   - `#matches`: the bot creates the private match threads here.
   - `#live-draft` (optional): live boards for spectators.
5. Turn on **Developer Mode** (Discord Settings → Advanced). Right-click the server, each channel
   and the role → **Copy ID**, and paste the IDs into `config.yaml`.

## Configuration

`config.yaml` (template: [config.example.yaml](config.example.yaml)):

| Setting | Meaning |
|---|---|
| `guild_id`, `admin_role_id`, `channels.*` | IDs from step 5 above. |
| `timers.*` | Draft step (45 s), side choice (2 min), no-show reminder/escalation (10/15 min), auto-confirm (10 min), rematch offer, thread archive. |
| `dev_mode` | Test mode (see [Test it alone](#test-it-alone-dev-mode)). **Off for real events.** |
| `upload_emojis` | Upload thumbnails as the bot's own emojis so menus show icons (up to 2000, no server boost needed). |
| `draft_order` | Optional custom order. The default is the agreed two-phase split. |

`.env` holds only `DISCORD_TOKEN`.

## Roster and thumbnails

- `data/roster.csv` has the columns `slug,name,element,role,image`. Only `name` and `element` are required.
  - The elements are Fire, Water, Grass, Ice, Electric, Rock, Wind, Holy and Dark.
  - Lines starting with `#` are comments.
  - The shipped list is **placeholder names**: replace it with the real roster.
- Put the thumbnails in `assets/aniimo/`, square PNGs of at least 128×128.
  - By default the file is `<slug>.png`; set `image` to use another file name.
  - Any Aniimo without an image gets a coloured badge with its initials.
- To check your thumbnails offline, run `run.bat preview` / `./run.sh preview`. It renders sample
  boards and a bracket with your roster into `preview/`.
- While the bot runs, `/roster reload` re-reads the file and images, and `/roster info` lists missing thumbnails.

Official Aniimo art belongs to its publisher, so `assets/aniimo/` is git-ignored. Use images you're allowed to use.

## Running a tournament (admins)

1. **`/tournament create name: AniiCup #1`** in `#admin` opens the step-1 panel. Pick the **player cap**
   (8, 16, 32, 64 or 128), the **format** and **Fearless**. The table shows every round with its Bo,
   the number of matches and the most games possible.

   | Format | Earlier rounds | Last 16 | Quarterfinal | Semifinal | Final |
   |---|---|---|---|---|---|
   | Quick | Bo1 | Bo1 | Bo1 | Bo1 | Bo3 |
   | Standard | Bo1 | Bo3 | Bo3 | Bo3 | Bo5 |
   | Championship | Bo3 | Bo3 | Bo3 | Bo5 | Bo5 |

2. **Next: add players**. Paste the CSV into `#admin` or attach a `.csv` file (or use `/tournament players`):

   ```csv
   discord_id,aniimo_uid,display_name
   412806339218145281,100234567,Luna
   523917440329256392,100345678,Kai
   ```

   The preview lists any bad rows: not a server member, duplicate IDs, or more players than the cap.
   Paste again to replace the list, then **Confirm**.
3. **Start tournament**. The bot shuffles the players, posts the bracket and Round 1 table in `#announce`,
   and opens a private thread for each match.
4. When a round finishes, `#admin` gets **▶ Start Round N**. Cap, format and Fearless are locked once
   the tournament starts.
5. Watch `#admin` for alerts: **no-show** (give the win / extend / DQ both), **dispute**
   (set the result / replay) and **Call admin**.
6. After the final, the bot posts the champion, the final bracket and pick/ban stats.

`/tournament status` lists the current round's matches with their IDs. `/tournament bracket` shows the
bracket, and `/tournament cancel` stops everything.

### Admin commands

Run these inside a match thread, or pass `match:<id>`.

| Command | What it does |
|---|---|
| `/admin undo` | Undo the last draft step (also works right after the draft) |
| `/admin pause` / `/admin resume` | Freeze or resume the draft timer (resume gives a full timer) |
| `/admin set-result winner:` | Set the winner of the current game |
| `/admin award winner:` | Give the whole match to a player (walkover) |
| `/admin replay mode:same_teams\|new_draft` | Replay the current game |
| `/admin extend minutes:` | Extend the ready check |
| `/admin dq player:` | Disqualify a player; their opponent advances, or walks through later |
| `/admin swap old: new: aniimo_uid:` | Replace a player who hasn't played yet |

## For players

1. You get pinged in your private match thread. Copy your opponent's **Aniimo UID**; player 1 hosts the in-game room.
2. Click **Ready**. The coin-flip winner picks **Side A** (first ban and pick) or **Side B** (counter-pick).
3. On your turn, click **🎯 Make your choice** → choose an element → choose an Aniimo → **Lock in**.
   You can also use **🔍 Search by name**. You have 45 seconds; on timeout a ban is skipped and a pick
   is random.
4. Play in Aniimo using your 4 picks, then **Report result**. Your opponent confirms or disputes.
   You can post a result screenshot in the thread and it's attached to the report.
5. **Rematch** replays the current game: same teams after a disconnect, or a new draft. The opponent
   must accept and the score doesn't change. **Next game** starts the next game of a Bo3/Bo5, and the
   loser of the last game picks the side.

`/profile`, `/leaderboard` and `/stats aniimo` work for everyone.

## Test it alone (dev mode)

Set `dev_mode: true`, then:

1. Paste a CSV with made-up IDs, e.g. `11,1,Test A` · `22,2,Test B` · `33,3,Test C`.
2. Start the tournament. Threads are created without those fake players.
3. As an admin, click their buttons yourself: Ready, side, Make your choice, Report and Confirm.
   The bot acts for the test player and tells you who.

You can mix in real accounts (e.g. a second Discord account) to check pings and private messages.
To test the timers quickly, set short values in `timers:`, e.g. `draft_step_seconds: 15`.
**Turn dev mode off for real events.**

## Data, restarts and backups

- **Database:** everything is stored in `data/aniimo.db` (SQLite). A backup is written to
  `data/backups/` at every start, and the last 10 are kept.
- **Restarts are safe:**
  - Buttons keep working, and open drafts get their turn message re-posted.
  - Any timer that ran out while the bot was offline restarts with its full time, because players
    couldn't click anything meanwhile.
- **Logs:** `logs/bot.log`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `aniimo-pvp check` shows ✗ | Fill in the missing ID or token it names. |
| Slash commands don't appear | Check that `guild_id` is right and the invite included `applications.commands`; restart the bot. |
| Pasting the CSV does nothing | Turn on Message Content Intent; paste in `#admin` as someone with the admin role. |
| "Couldn't create a private thread" | Give the bot Create Private Threads + Send Messages in Threads in `#matches`. |
| Admins can't see match threads | Give the admin role Manage Threads in `#matches`. |
| Menus show element icons instead of Aniimo icons | The emoji upload is still running or failed; see `/roster info` and the log. |

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q            # full suite: rules, services, rendering, Discord layer with fakes
ruff check src tests
```

- **Layout:** `src/aniimo_pvp/` contains `domain/` (pure rules), `services/` (use cases + timers),
  `render/` (Pillow images) and `bot/` (discord.py).
- **Fonts:** Inter and Space Grotesk ship under the SIL Open Font License (`assets/fonts/`).
