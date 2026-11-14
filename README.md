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
- **Friendly duels**: anyone can `/duel` a friend in `#friendly` for a 1v1 draft with no results or
  ratings, and start a new draft as often as they like.
- **Real thumbnails and element signs** downloaded from the official wiki with one command.
- **Extras**: a live draft feed, pick/ban stats, an optional Fearless mode, and a rating leaderboard.

**Want to try it? Follow the [step-by-step testing guide](docs/TESTING.md).**

Design docs: [system design](docs/SYSTEM_DESIGN.md) · [implementation plan](docs/IMPLEMENTATION.md) ·
[approved mockups](https://claude.ai/artifact/7112nacYyipqnhKMCC3ELu)

---

## Quick start

Want a guided walk-through with what to expect at every step? Use the [testing guide](docs/TESTING.md).
The short version:

1. Install **Python 3.11+** (Windows: from python.org, tick "Add Python to PATH").
2. Do the [Discord setup](#discord-setup) below, which takes about 10 minutes.
3. Open a terminal in the project folder and run the first start. It installs everything, creates `config.yaml`,
   `.env` and `data/roster.csv`, **downloads the Aniimo roster, thumbnails and element icons** from the official
   wiki (about 20 seconds, 120 Aniimo), then stops on purpose.

   **Windows** (Command Prompt or PowerShell)

   ```
   .\run.bat
   ```

   **macOS / Linux**

   ```
   ./run.sh
   ```

4. Put your bot token in `.env` (one line, no quotes):

   ```
   DISCORD_TOKEN=PASTE_YOUR_TOKEN_HERE
   ```

   Then fill in the IDs in `config.yaml` (each setting has a comment explaining it).
5. Check your settings. Fix anything marked ✗ and run it again until it says `All good`:

   **Windows**

   ```
   .\run.bat check
   ```

   **macOS / Linux**

   ```
   ./run.sh check
   ```

6. Start the bot (leave the window open; `Ctrl+C` stops it):

   **Windows**

   ```
   .\run.bat
   ```

   **macOS / Linux**

   ```
   ./run.sh
   ```

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
   - `#friendly` (optional): where anyone can start a [friendly duel](#friendly-duels). The bot
     opens the duel rooms as private threads here.
5. Turn on **Developer Mode** (Discord Settings → Advanced). Right-click the server, each channel
   and the role → **Copy ID**, and paste the IDs into `config.yaml`.

## Configuration

`config.yaml` (template: [config.example.yaml](config.example.yaml)):

| Setting | Meaning |
|---|---|
| `guild_id`, `admin_role_id`, `channels.*` | IDs from step 5 above. |
| `timers.*` | Draft step (45 s), side choice (2 min), no-show reminder/escalation (10/15 min), auto-confirm (10 min), rematch offer, thread archive. Friendly duels: challenge expiry (10 min) and idle close (30 min). |
| `channels.friendly` | Optional. The channel where `/duel` works. `0` turns friendly duels off. |
| `dev_mode` | Test mode (see [Test it alone](#test-it-alone-dev-mode)). **Off for real events.** |
| `upload_emojis` | Upload thumbnails as the bot's own emojis so menus show icons (up to 2000, no server boost needed). |
| `draft_order` | Optional custom order. The default is the agreed two-phase split. |

`.env` holds only `DISCORD_TOKEN`.

## Roster and thumbnails

The roster, the thumbnails and the nine element icons come from the **official Aniimo wiki**
(<https://wiki.aniimo.com/>). One command downloads them to your PC:

**Windows**

```
.\run.bat fetch-assets
```

**macOS / Linux**

```
./run.sh fetch-assets
```

- It writes `data/roster.csv` (name, element, role), `assets/aniimo/<slug>.png` (a square thumbnail per Aniimo)
  and `assets/elements/<element>.png` (the element signs, in the wiki's colours). The first run does this for you.
- It only talks to `wiki.aniimo.com` and the wiki's own image servers, checks every file, and re-saves it as a
  plain PNG. Files you already have are skipped, so re-running is quick.
- **Run it again when new Aniimo are added.** `--refresh` downloads every thumbnail again.
- Your edits are safe. If `data/roster.csv` already exists and you changed it, the new list is saved as
  `data/roster.wiki.csv` instead. Use `--overwrite-roster` to replace yours (a backup is kept).
- The wiki lists every evolution stage as its own Aniimo, so the list has 120 entries. **Delete a row** from
  `data/roster.csv` to take an Aniimo out of the draft pool.
- Aniimo with two elements, like Fire/Dark, appear under both elements in the picker, with both signs on the board.

The picker shows each Aniimo's thumbnail and each element's sign as menu icons. The bot uploads them as its own
emojis the first time it connects (no server boost needed), and `/roster info` shows how that went.

Other things to know:

- `data/roster.csv` has the columns `slug,name,element,role,image`. Only `name` and `element` are required.
  Lines starting with `#` are comments, and two elements are written like `Fire/Dark`.
- To use your own picture, put a PNG in `assets/aniimo/` named `<slug>.png`, or set the `image` column.
  Any Aniimo without an image gets a coloured badge with its initials. The same goes for an element sign:
  a file in `assets/elements/` overrides the downloaded one.
- To check how everything looks without Discord, run `preview` the same way (`.\run.bat preview` on Windows,
  `./run.sh preview` on macOS/Linux). It renders a sample board and bracket with your roster into `preview/`.
- While the bot runs, `/roster reload` re-reads the file and images.
- The Aniimo art belongs to its publisher. The images are downloaded to your own PC for your tournaments and are
  not part of this repository (`assets/` is git-ignored). Please don't commit or redistribute them.

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

## Friendly duels

A quick 1v1 draft with a friend, outside any tournament. It's always 2 players, one draft at a
time, and **nothing is recorded**: no results, ratings, stats or announcements.

1. In `#friendly`, run `/duel` and pick your friend. Option `fearless`: set it to True and you can't
   re-pick an Aniimo you played in an earlier draft of the same duel.
2. Your friend gets a challenge card with **Accept** / **Decline**. You can **Cancel** it. It expires
   after 10 minutes.
3. Accepting opens a private room thread in `#friendly`. It works like a match room: **Ready**, coin
   flip, side choice, then the draft with the same picker and board.
4. After the draft, play it in Aniimo. Then click **🔄 New draft** to go again (the other player
   chooses the side this time) or **🏁 End duel**. Each finished board stays in the room.

Good to know:

- You can have one open duel at a time.
- `/register uid:` saves your Aniimo UID so it shows on your duel room card. Tournaments still use
  the UID from the admin's player list.
- Mention a friend in the room to let them watch. Only the two players can click the buttons.
- A duel closes by itself if you're not both ready within 15 minutes, or nobody starts a new draft
  for 30 minutes. The thread is archived a few minutes after the duel ends.
- With dev mode on, `/duel-test` starts a duel against a test player, and you click for both sides.

## Test it alone (dev mode)

The full walkthrough, with what you should see at each step, is in the [testing guide](docs/TESTING.md).
In short:

1. In `config.yaml` turn on dev mode (and, if you like, short timers so tests are quick):

   ```yaml
   dev_mode: true
   timers:
     draft_step_seconds: 30
     side_choice_seconds: 30
   ```

2. Restart the bot, run `/tournament create`, and paste made-up players when it asks for them:

   ```csv
   11,1001,Test A
   22,1002,Test B
   33,1003,Test C
   44,1004,Test D
   ```

3. Start the tournament. The match threads are created without those made-up players.
4. As an admin, click their buttons yourself: Ready, side, Make your choice, Report and Confirm.
   The bot acts for the test player and tells you who.

You can mix in real accounts (for example a second Discord account) to check pings and private messages.
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
| `/duel` says friendly duels aren't set up | Put the `#friendly` channel ID in `channels.friendly` and restart the bot. |
| `/duel` says "Duels start in #friendly" | Run it in that channel, not in a thread or another channel. |
| No thumbnails, or `fetch-assets` fails | Check your internet connection and run `fetch-assets` again. If the wiki's page layout changed, it says so and the bot keeps using coloured badges. |
| Menus show plain emojis instead of Aniimo icons | The emoji upload is still running or failed; see `/roster info` and the log. |

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q            # full suite: rules, services, rendering, Discord layer with fakes
ruff check src tests
```

- **Layout:** `src/aniimo_pvp/` contains `domain/` (pure rules), `services/` (use cases + timers),
  `render/` (Pillow images), `bot/` (discord.py) and `wiki.py` (the `fetch-assets` downloader).
- **Fonts:** Inter and Space Grotesk ship under the SIL Open Font License (`assets/fonts/`).
