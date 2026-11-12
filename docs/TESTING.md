# Testing guide

Follow this from top to bottom and you will have run a full test tournament. Every step says what to **do**
and what you should **see**. If something looks different, jump to [Troubleshooting](#troubleshooting).

The automated tests (`pytest`) already check the rules, timers and message logic with a pretend Discord.
**This guide checks what only a real Discord server can show:** private threads, pings, menus and buttons on
your phone, emoji uploads, and rate limits.

| Part | What it proves | Time |
|---|---|---|
| [1. Setup](#1-setup) | You can run the bot | 30 min |
| [2. Smoke test](#2-start-the-bot-and-smoke-test) | It connects and the commands exist | 5 min |
| [3. Test 1: solo run](#3-test-1-a-whole-tournament-alone) | The whole flow with made-up players | 30 min |
| [4. Test 2: two accounts](#4-test-2-two-real-accounts) | Pings, private threads, permissions | 20 min |
| [5. Test 3: timers and admin tools](#5-test-3-timers-and-admin-tools) | No-show, dispute, undo, DQ, swap | 20 min |
| [6. Test 4: restart](#6-test-4-restart-in-the-middle-of-a-draft) | Nothing is lost when the bot stops | 5 min |
| [7. Test 5: big bracket](#7-test-5-a-big-bracket) | Many threads at once, rate limits | 10 min |

**Short on time?** Do parts 1 to 3 (about 60 minutes). That already covers the whole tournament flow.

## How to read this guide

- **Do:** is what you do. ✅ **Expect:** is what you should see. If you don't see it, note the step number
  (like "3.4 step 6") and check [Troubleshooting](#troubleshooting).
- **Code blocks:** click the copy icon at the block's top-right corner, then paste. Words in
  `CAPITALS`, like `YOUR_APP_ID`, are placeholders you replace.
- **Windows and macOS/Linux** commands are shown as separate blocks. Copy the one for your computer.
- **Typing a slash command:** click the message box in Discord, type the command, pick it from the popup,
  fill in each option Discord shows, then press Enter. Here the command is in a block and its options are listed under it.
- **Buttons and menus** you click are in **bold**.
- **Panel** = the message with buttons in a match thread. **Board** = the picture of the draft.
  **Only you can see this** = a private reply that appears just for you.
- A message that starts with ⚠️ is the bot working correctly and refusing an action. It is not an error.

---

## 1. Setup

### A. Discord (a test server of your own)

1. Open <https://discord.com/developers/applications> and click **New Application**.
2. Go to **Bot**. Click **Reset Token** and copy the token somewhere safe (you need it in step B4).
   Turn on **Message Content Intent** and save.
3. Copy your **Application ID** (page *General Information*). Put it in place of `YOUR_APP_ID` below,
   open the link in your browser, choose your test server and click **Authorise**:

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=395137502272
   ```

4. In the server create these channels: `#admin`, `#announce`, `#matches` and `#live-draft`. Create a role
   named `Tournament Admin` and give it to yourself. (The server owner and anyone with the *Administrator*
   permission count as admins automatically.)
5. Give the `Tournament Admin` role the **Manage Threads** permission in `#matches`, or you won't see the private
   match threads. (The owner already has it.)
6. Turn on **Developer Mode**: Discord **Settings → Advanced → Developer Mode**. Now you can copy IDs:

   | ID | How to copy it |
   |---|---|
   | Server | Right-click the server icon → **Copy Server ID** |
   | Channel | Right-click the channel → **Copy Channel ID** |
   | Role | Server Settings → **Roles** → right-click the role → **Copy Role ID** |
   | A person | Right-click their name → **Copy User ID** |

7. Copy these 6 IDs into a note: the server, the 4 channels and the `Tournament Admin` role.

### B. Your PC

**B1. Install Python 3.11 or newer** from <https://www.python.org/downloads/>
(Windows: tick **Add Python to PATH**). Check it in a terminal:

**Windows**

```
python --version
```

**macOS / Linux**

```
python3 --version
```

✅ **Expect:** `Python 3.11.x` or a higher number.

**B2. Get the code.**

```
git clone -b ccr-d33f10a4-hir0z0 https://github.com/lunaxoniichan/aniimo-pvp.git
cd aniimo-pvp
```

No git? Download <https://github.com/lunaxoniichan/aniimo-pvp/archive/refs/heads/ccr-d33f10a4-hir0z0.zip>,
unzip it and open that folder in a terminal.

> **Opening a terminal in the folder.** Windows: open the folder in File Explorer, click its address bar,
> type `cmd` and press Enter. macOS: right-click the folder → **New Terminal at Folder**. Linux: open your
> terminal and `cd` into the folder. Run every command below from this folder.

**B3. First run.** This installs everything, creates `config.yaml`, `.env` and `data/roster.csv`, and downloads
the 120 Aniimo with thumbnails and element icons. It takes a few minutes the first time and then stops on purpose.

**Windows**

```
.\run.bat
```

**macOS / Linux** (if you get "permission denied", use `bash run.sh`)

```
./run.sh
```

✅ **Expect** these lines near the end:

```
✓ 120 Aniimo: 120 thumbnails downloaded, 0 already had
✓ Element icons: 9/9
✓ Roster: ... (written)
Fill in config.yaml and .env, then run ... again.
```

**B4. Put the bot token in `.env`.** Open the file:

**Windows**

```
notepad .env
```

**macOS**

```
open -e .env
```

**Linux**

```
nano .env
```

Replace everything in it with this one line (your token, no quotes, no spaces), then save:

```
DISCORD_TOKEN=PASTE_YOUR_TOKEN_HERE
```

**B5. Fill in `config.yaml`.** Open it the same way (`notepad config.yaml`, `open -e config.yaml` or
`nano config.yaml`). Replace **everything** in it with the block below, then replace each `0` marked
with an arrow by the matching ID from your note, and save:

```yaml
guild_id: 0              # <-- your server ID
admin_role_id: 0         # <-- the Tournament Admin role ID
channels:
  admin: 0               # <-- #admin
  announce: 0            # <-- #announce
  matches: 0             # <-- #matches
  live_draft: 0          # <-- #live-draft

dev_mode: true           # test mode: made-up players allowed, you click buttons for them
upload_emojis: true

timers:                  # short timers so tests are quick
  draft_step_seconds: 30      # use 15 only when you want to watch a timeout
  side_choice_seconds: 30
  noshow_reminder_minutes: 1
  noshow_escalate_minutes: 2
  noshow_extend_minutes: 10
  auto_confirm_minutes: 1
  rematch_offer_seconds: 60
  thread_archive_minutes: 2
```

**B6. Check everything.**

**Windows**

```
.\run.bat check
```

**macOS / Linux**

```
./run.sh check
```

✅ **Expect** every line to start with ✓ (or be indented info), ending with `All good`:

```
✓ Roster: 120 Aniimo, 120 with thumbnails, elements: Fire, Water, Grass, Ice, Electric, Rock, Wind, Holy, Dark
✓ Element icons: 9/9
✓ Fonts in ...
✓ Draft order: 12 steps
  Dev mode: ON (test players allowed, admins can act for them)
All good. Start the bot with: aniimo-pvp run
```

A line starting with ✗ names exactly what to fix (for example a missing channel ID). Fix it and run `check` again.

**B7. Look at a sample board.**

**Windows**

```
.\run.bat preview
start preview\board_complete.png
```

**macOS**

```
./run.sh preview
open preview/board_complete.png
```

**Linux**

```
./run.sh preview
xdg-open preview/board_complete.png
```

✅ **Expect** a dark board with two sides (blue and orange), real Aniimo pictures, and round element signs
next to each name.

---

## 2. Start the bot and smoke test

**2.1 Start the bot.** Leave this window open the whole time. Press `Ctrl+C` in it to stop the bot.

**Windows**

```
.\run.bat
```

**macOS / Linux**

```
./run.sh
```

✅ **Expect** lines like these (each starts with a date and time). The emoji line appears about a minute later:

```
INFO    aniimo_pvp.bot.client: Synced 6 slash commands to guild 123456789012345678
INFO    aniimo_pvp.bot.client: Logged in as YourBotName (111111111111111111). Dev mode: True
INFO    aniimo_pvp.bot.emojis: Emoji sync: 129 uploaded, 0 unchanged, 0 failed
```

**2.2 Slash commands exist.**
**Do:** in `#admin`, type `/tournament` (don't press Enter).
✅ **Expect:** the popup lists `create`, `players`, `start`, `status`, `bracket` and `cancel`.

**2.3 Roster and emoji status.**
**Do:** run this in any channel:

```
/roster info
```

✅ **Expect** these facts (as a message only you can see):

```
Aniimo: 120
Elements: Fire, Water, Grass, Ice, Electric, Rock, Wind, Holy, Dark
Missing thumbnails: None
Emojis: 129 uploaded, 0 unchanged, 0 failed
```

The *Emojis* line says `not synced` until the upload finishes. Wait a minute and run it again.

---

## 3. Test 1: a whole tournament alone

`dev_mode: true` lets you play every player yourself. When you click a button for a made-up player the bot
tells you who you acted for, for example *"(dev mode: acting for Test A)"*.

### 3.1 Create the tournament (in `#admin`)

1. **Do:** run this command:

   ```
   /tournament create
   ```

   Option `name`: type `Test Cup`.

   ✅ **Expect:** a panel titled "New tournament · Test Cup" with "STEP 1 OF 2", a rounds table, and three
   menus: **Players**, **Format**, **Fearless**.

2. **Do:** set **Players** to `Up to 128 players`, then **Format** to `Championship`.

   ✅ **Expect:** the table changes each time (down to "Round of 128" and a long list of rounds, and an
   "up to … games" line).

3. **Do:** now set them back to **Up to 8 players**, **Quick**, and **Fearless: off**.

   ✅ **Expect:** the table shows `Quarterfinal`, `Semifinal` and `Final` with `Bo1, Bo1, Bo3`.

4. **Do:** click **Next: add players**.

   ✅ **Expect:** a new message "STEP 2 OF 2 · PLAYERS".

### 3.2 Add the players (in `#admin`)

**Do:** send this as a normal message (copy, paste, Enter):

```csv
11,1001,Test A
22,1002,Test B
33,1003,Test C
44,1004,Test D
```

✅ **Expect:** a "Player list preview" with `Valid players 4`, `Bracket 4 slots · 0 byes`, the rounds
`Semifinal Bo1 · Final Bo3`, and a footer saying 4 test players.

**Do:** click **Confirm 4 players**.

✅ **Expect:** "4 players confirmed" with a **Start tournament** button.

### 3.3 Start

**Do:** click **Start tournament**.

✅ **Expect:**
- in `#announce`: a bracket picture and a "Round 1 · Semifinal · Bo1" list;
- in `#matches`: two private threads named like `R1 · M1 · Test A vs Test D`;
- in `#live-draft`: a status message listing both matches.

Run `/tournament status` any time to list the matches with their numbers.

### 3.4 Play match 1 (open its thread)

1. **Do:** read the first message.

   ✅ **Expect:** both players' Aniimo UIDs in copyable boxes, and "Test A creates the in-game room".

2. **Do:** click **Ready** twice.

   ✅ **Expect:** first "You're ready. Waiting for …", then "Both players are ready!". The panel changes to a
   coin flip ("… wins the toss").

3. **Do:** click **Take Side A · first pick**.

   ✅ **Expect:** a small grey line "… took Side A … The draft starts now", then a **turn message** with the
   board picture and a **🎯 Make your choice** button.

4. **Do:** click **Make your choice**.

   ✅ **Expect:** a message that only you can see, with an **element menu** (each element has its sign).

5. **Do:** choose an element, then an Aniimo.

   ✅ **Expect:** the Aniimo menu lists only that element and shows each Aniimo's picture. After you choose, a
   large thumbnail and its element signs appear.

6. **Do:** click **Lock in**.

   ✅ **Expect:** "🔒 You banned …". The old turn message disappears, a **new** one appears with the updated
   board, and the board in `#live-draft` updates too.

7. **Do:** next turn, click **Make your choice → 🔍 Search by name** and type `emb` (or part of any name).

   ✅ **Expect:** a short list of names that contain what you typed.

8. **Do:** keep going until all 12 steps are done.

   ✅ **Expect:** the order is ban, ban, pick, pick, pick, pick, ban, ban, pick, pick, pick, pick. An Aniimo
   that is banned or picked disappears from the menus. At the end: "the draft is complete" and a panel with
   **Report result**, **Rematch** and **Call admin**.

**Also check a dual-element Aniimo**, for example `Inferlupa` (Fire/Dark). ✅ **Expect:** it appears under both
the Fire and the Dark element menus, and on the board both signs show.

### 3.5 Rematch (before reporting)

1. **Do:** click **Rematch → Replay same teams**, then **Accept**.

   ✅ **Expect:** "Game 1 is replayed with the same teams (attempt 2)". The board is unchanged and the panel
   is back to **Report result**.

2. **Do:** click **Rematch → New draft**, then **Accept**.

   ✅ **Expect:** the draft starts again at step 1. Redo it quickly, it's only a test.

### 3.6 Report the result

1. **Do:** click **Report result** and choose a winner.

   ✅ **Expect:** a "Result reported" panel with **Confirm** and **Dispute**, and "Auto-confirms in about a minute".

2. **Do:** click **Confirm**.

   ✅ **Expect:** "🏆 … wins 1–0" with "Next: … plays … in the Final". The thread is archived after 2 minutes.
   There is **no Next game button** because this match is Bo1.

### 3.7 Dispute (match 2)

**Do:** play match 2 the same way, but after **Report result** click **Dispute**.

✅ **Expect:** a message in `#admin` with one button per player and **Replay game**.

**Do:** click one of the player buttons.

✅ **Expect:** the match ends with the result you picked.

### 3.8 Round 2 and the final

1. ✅ **Expect** in `#admin`: "Round 1 complete" with a **Start Round 2** button, and an updated bracket in `#announce`.

2. **Do:** click **Start Round 2**.

   ✅ **Expect:** one new thread for the final, marked **Bo3**.

3. **Do:** Ready, side and draft as before, play game 1 and confirm it.

   ✅ **Expect:** "Game 1 confirmed", the score `1 – 0`, and a **Next game** button.

4. **Do:** click **Next game**.

   ✅ **Expect:** "loser's choice": the loser of game 1 picks the side. Play until someone has two wins.

5. ✅ **Expect** in `#announce`: "🏆 … wins Test Cup", the final bracket and pick/ban stats.

6. **Do:** try these (`/profile` needs a real server member, so it comes in Test 2):

   ```
   /leaderboard
   ```

   ```
   /stats aniimo
   ```

   ✅ **Expect:** ratings and pick/ban numbers from the games you just played.

### 3.9 Fearless

**Do:** make another tournament: `/tournament create` → name `Fearless Cup` → **Up to 8 players**, **Quick**,
**Fearless: on** → **Next: add players**, then paste only two players:

```csv
11,1001,Test A
22,1002,Test B
```

Confirm, start, play game 1 (final = Bo3), confirm it, then click **Next game**.

✅ **Expect** on your first *pick* of game 2: "Fearless: N Aniimo you already played are locked for you". Those
Aniimo are missing from your menu. Your opponent can still pick them, and bans work as usual.

---

## 4. Test 2: two real accounts

This checks what dev mode skips. You need a second Discord account (a friend or your own alt) that has joined the server.

1. **Do:** `/tournament cancel` the old tournament if one is open, then create a new one (`/tournament create`,
   **Up to 8 players**, **Quick**). When it asks for players, paste this after replacing `YOUR_ID` and `OTHER_ID`
   with the two real User IDs (see the table in setup step A6):

   ```csv
   YOUR_ID,1001,Me
   OTHER_ID,1002,Other
   ```

2. **Do:** confirm and start.

   ✅ **Expect:** a private thread that **both of you** can see, with a ping for each of you. A member who isn't
   a player must **not** see the thread.

3. **Do:** each person clicks their own **Ready**, then try these on purpose:

   | Who clicks | What | ✅ Expect |
   |---|---|---|
   | The wrong person | **Take Side A** | "The other player chooses the side" |
   | The wrong person | **Make your choice** | "It's … turn to ban/pick" |
   | An admin who isn't a player | **Report result** | "Only the players can report the result" |
   | The reporter | **Confirm** on their own report | "Only the other player can confirm this result" |

4. **Do:** draft once on your **phone**.

   ✅ **Expect:** the menus, the preview and **Lock in** are comfortable to use.

5. **Do:** post any picture in the thread while the result is waiting for confirmation.

   ✅ **Expect:** a 📎 reaction on your message, and the picture shows on the "Result reported" panel.

6. **Do:** after the match, try:

   ```
   /profile
   ```

   ✅ **Expect:** your rating and record.

---

## 5. Test 3: timers and admin tools

Use made-up players (`Test A` to `Test D`) in dev mode again. Run the `/admin` commands **inside the match
thread**, so the bot knows which match you mean.

### 5.1 Draft timeout

**Do:** start a draft and don't click anything for 30 seconds.

✅ **Expect:** a grey line "⏱️ … ran out of time". A ban is skipped ("No ban" on the board). A pick is chosen at
random and tagged **AUTO**.

### 5.2 Side-choice timeout

**Do:** reach the coin flip and wait 30 seconds.

✅ **Expect:** "… didn't choose in time, so … is Side A" and the draft starts.

### 5.3 No-show

**Do:** open a match (start a round) and click nothing.

✅ **Expect:** after 1 minute a reminder in the thread; after 2 minutes an alert in `#admin` with
**Give win to …**, **Extend 10 min** and **DQ both**. Try **Extend 10 min** and check the thread says so.

### 5.4 Auto-confirm

**Do:** report a result and wait one minute.

✅ **Expect:** "Game … confirmed (auto-confirmed)".

### 5.5 Pause and resume

**Do:** during a draft run:

```
/admin pause
```

Wait longer than the step time, then run:

```
/admin resume
```

✅ **Expect:** while paused, **Make your choice** is greyed out, the board says "PAUSED BY AN ADMIN" and nothing
times out. After resume the step gets a full timer.

### 5.6 Undo

**Do:** during a draft run:

```
/admin undo
```

✅ **Expect:** the last ban or pick disappears from the board, with a line "↩️ An admin undid step …".

### 5.7 Set the result, award a match, replay

**Do:** in a game that is being played or disputed run `/admin set-result`, and pick the winner (option `winner`).
✅ **Expect:** the game is decided.

**Do:** in a match that hasn't started run `/admin award`, and pick the winner.
✅ **Expect:** the match ends as a walkover. Ratings don't change.

**Do:** in a game being played run:

```
/admin replay
```

Option `mode`: `same_teams`.
✅ **Expect:** a replay (attempt 2) with the same teams.

### 5.8 Disqualify and swap

**Do:** run `/admin dq` and pick a player (option `player`).
✅ **Expect:** the opponent advances (at once if their match is open, otherwise when it comes up).

**Do:** for a player whose match hasn't started, run `/admin swap` with options `old` (pick the player),
`new` (a user ID or mention) and `aniimo_uid` (any number).
✅ **Expect:** the thread shows the new name with a fresh UID card.

### 5.9 Call admin

**Do:** click **Call admin** in any match.

✅ **Expect:** a ping for the admin role in the thread and a line in `#admin`.

---

## 6. Test 4: restart in the middle of a draft

1. **Do:** start a draft and lock in two or three steps.
2. **Do:** stop the bot (`Ctrl+C` in its window), wait about a minute, and start it again:

   **Windows**

   ```
   .\run.bat
   ```

   **macOS / Linux**

   ```
   ./run.sh
   ```

3. ✅ **Expect** in the window a line like `Restarted 1 timers that were due while the bot was offline`.
   In the thread a **fresh turn message** with a full timer. The old buttons still work and the draft
   carries on from the same step.

---

## 7. Test 5: a big bracket

1. **Do:** cancel the current tournament (`/tournament cancel`). Create one with **Up to 32 players** and
   **Standard**.
2. **Do:** make 32 made-up players. This command copies them to your clipboard, so just paste into `#admin`:

   **Windows (PowerShell)**

   ```
   1..32 | % { "$($_*11),$(1000+$_),Test $_" } | clip
   ```

   **macOS**

   ```
   python3 -c "print('\n'.join(f'{i*11},{1000+i},Test {i}' for i in range(1,33)))" | pbcopy
   ```

   **Linux** (prints them, then select and copy)

   ```
   python3 -c "print('\n'.join(f'{i*11},{1000+i},Test {i}' for i in range(1,33)))"
   ```

3. **Do:** confirm, then **Start tournament**.

   ✅ **Expect:** 16 threads in `#matches` within about a minute, a bracket picture in `#announce`, and **no
   errors** in the bot window. Rate limiting would show as `429` or `rate limited` lines.

---

## What only your server can confirm

Tick each one as you go. These are the parts the automated tests cannot check.

- [ ] Private threads are created and the right people are added (Test 2).
  - If not: give the bot **Create Private Threads** and **Send Messages in Threads** in `#matches`.
- [ ] Admins can see the threads.
  - If not: give the admin role **Manage Threads** in `#matches`.
- [ ] Pings reach the players and the admin role.
  - If not: make the role mentionable, or give the bot **Mention All Roles**.
- [ ] The pasted CSV is read.
  - If not: turn on **Message Content Intent** and paste as an admin in `#admin`.
- [ ] Menus show Aniimo pictures and element signs.
  - If not: run `/roster info` and look at the *Emojis* line; read the log if it shows failures.
- [ ] Buttons, menus and the picker are comfortable on a phone.
  - If not: tell me which screen is awkward.
- [ ] 16 or more threads open without rate-limit errors (Test 5).
  - If not: tell me and I will slow the creation down.

## Troubleshooting

Details of every error are in `logs/bot.log` (in the project folder).

| Problem | What to do |
|---|---|
| `check` shows ✗ | Fill in the token or ID it names, then run `check` again |
| Slash commands missing | Check `guild_id`, make sure you invited the bot with the link in step A3, restart the bot, wait a minute |
| Nothing happens when I paste the CSV | Message Content Intent is off, or you aren't an admin, or the tournament is already past the player step |
| "Only tournament admins can do that" | You need the `Tournament Admin` role (or the Administrator permission) |
| "… is still running. Finish or /tournament cancel it first" | Only one tournament runs at a time: finish it or run `/tournament cancel` |
| No Aniimo pictures | Run `fetch-assets` (below), then `/roster reload` |
| A button says "This interaction failed" | Send me the last 30 lines of `logs/bot.log` |

To download the roster and pictures again:

**Windows**

```
.\run.bat fetch-assets
```

**macOS / Linux**

```
./run.sh fetch-assets
```

## Reset and going live

**Start over** (stop the bot first; a backup of the database is made every time the bot starts):

**Windows**

```
del data\aniimo.db
```

**macOS / Linux**

```
rm data/aniimo.db
```

**Before a real event,** in `config.yaml` set `dev_mode: false`, delete the whole `timers:` section (the defaults are
45 s per step and 10/15 minutes for no-shows), and restart. Real players must be members of the server; the CSV
rejects anyone who isn't.
