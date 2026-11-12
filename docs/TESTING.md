# Testing guide

Step by step, from nothing to a finished test tournament. Do the tests in order. Each step says what
you should **see**. If something differs, check [Troubleshooting](#troubleshooting) and `logs/bot.log`.

The automated tests (`pytest`) already cover the rules, the timers and the message logic with fake Discord
objects. **This guide covers what only a real Discord server can show**: private threads, pings, menus
and buttons on your phone, emoji uploads and rate limits.

| Test | What it proves | Time |
|---|---|---|
| [Setup](#1-one-time-setup) | You can run the bot | 30 min |
| [Smoke test](#2-start-the-bot-and-smoke-test) | It connects and the commands exist | 5 min |
| [Test 1: solo run](#3-test-1-a-whole-tournament-alone) | The whole flow, using made-up players | 30 min |
| [Test 2: two accounts](#4-test-2-two-real-accounts) | Pings, private threads, permissions | 20 min |
| [Test 3: timers and admin tools](#5-test-3-timers-and-admin-tools) | No-show, dispute, undo, DQ, swap | 20 min |
| [Test 4: restart](#6-test-4-restart-in-the-middle-of-a-draft) | Nothing is lost when the bot stops | 5 min |
| [Test 5: big bracket](#7-test-5-a-big-bracket) | Many threads at once, rate limits | 10 min |

## 1. One-time setup

**On Discord** (a test server of your own; nobody else needs to be in it yet)

1. Open <https://discord.com/developers/applications> → **New Application**.
2. **Bot** page: **Reset Token** and copy it. Turn on **Message Content Intent**.
3. Open this link (put your Application ID from *General Information* in place of `YOUR_APP_ID`), choose your test
   server and **Authorise**:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=395137502272`
4. In the server create the channels `#admin`, `#announce`, `#matches`, `#live-draft`, and a role `Tournament Admin`.
   Give yourself the role. (The server owner and anyone with the *Administrator* permission count as admins too.)
5. The admin role needs **Manage Threads** in `#matches`, or you won't see the private match threads.
   The owner already has it.
6. Discord **Settings → Advanced → Developer Mode ON**. Right-click the server, the 4 channels and the role →
   **Copy ID**, and keep them in a note.

**On your PC**

1. Install **Python 3.11 or newer** (Windows: tick "Add Python to PATH").
2. Get the code, for example:
   `git clone -b ccr-d33f10a4-hir0z0 https://github.com/lunaxoniichan/aniimo-pvp.git`, then open that folder.
3. Run `run.bat` (Windows) or `./run.sh` (macOS/Linux). It installs everything, creates `config.yaml`, `.env` and
   `data/roster.csv`, **downloads the 120 Aniimo with thumbnails and element icons**, then stops. This takes a few minutes the first time.
4. Put the token in `.env`: `DISCORD_TOKEN=...`
5. Edit `config.yaml`: fill in `guild_id`, `admin_role_id` and the four `channels`. For testing also set:

   ```yaml
   dev_mode: true            # made-up players allowed; as admin you click buttons for them
   timers:
     draft_step_seconds: 30       # use 15 only when you want to watch a timeout
     side_choice_seconds: 30
     noshow_reminder_minutes: 1
     noshow_escalate_minutes: 2
     auto_confirm_minutes: 1
     rematch_offer_seconds: 60
     thread_archive_minutes: 2
   ```

6. Run `run.bat check`. **Expect** every line to start with ✓ and `All good`.
7. Run `run.bat preview`, then open `preview/board_complete.png`.
   **Expect** a board with real Aniimo pictures and element signs.

## 2. Start the bot and smoke test

1. Run `run.bat` (leave the window open; Ctrl+C stops it).
   **Expect** in the window: `Synced 6 slash commands`, `Logged in as ...`, then later `Emoji sync: 129 uploaded ...`.
2. In Discord type `/tournament` in `#admin`.
   **Expect** the commands `create`, `players`, `start`, `status`, `bracket` and `cancel` to appear.
3. Run `/roster info`.
   **Expect** `Aniimo 120`, 9 elements, `Missing thumbnails: None`. The *Emojis* line says `not synced` or shows counts
   until the upload finishes, which takes about a minute the first time. Run it again later.

## 3. Test 1: a whole tournament alone

With `dev_mode: true` you play every player yourself. The bot tells you who you're acting for, for example
*"(dev mode: acting for Test A)"*.

**3.1 Create** (in `#admin`)

1. `/tournament create name: Test Cup`.
   **Expect** a panel "New tournament · Test Cup · STEP 1 OF 2" with a rounds table and menus for **Players**,
   **Format** and **Fearless**.
2. Change **Players** to 128, then **Format** to Championship.
   **Expect** the table to update each time (Round of 128 … Final, and the "up to N games" line). Then set it back
   to **8 players, Quick, Fearless off**.
3. Click **Next: add players**.
   **Expect** a "STEP 2 OF 2 · PLAYERS" message.

**3.2 Add players** (paste this into `#admin` as a normal message)

```
11,1001,Test A
22,1002,Test B
33,1003,Test C
44,1004,Test D
```

**Expect** a "Player list preview": 4 valid players, bracket 4 slots, 0 byes, `Semifinal Bo1 · Final Bo3`, a footer about
4 test players. Click **Confirm 4 players**.
**Expect** "4 players confirmed" with a **Start tournament** button.

**3.3 Start**

1. Click **Start tournament**.
2. **Expect** in `#announce`: a bracket picture and a "Round 1 · Semifinal · Bo1" table.
   **Expect** in `#matches`: two private threads named like `R1 · M1 · Test A vs Test D`.
   **Expect** in `#live-draft`: a status message listing both matches.
   `/tournament status` lists the matches with their numbers.

**3.4 Play match 1** (open its thread)

| Step | You do | You should see |
|---|---|---|
| 1 | Read the first message | Both players' Aniimo UIDs in copyable boxes; "Test A creates the in-game room" |
| 2 | Click **Ready** twice | First: "You're ready. Waiting for ..." Second: "Both players are ready!" and the panel switches to the coin flip |
| 3 | Click **Take Side A · first pick** | A grey line "X took Side A ... The draft starts now" and a **turn message with the board picture** and a **🎯 Make your choice** button |
| 4 | Click **Make your choice** | A private message (only you see it) with an **element menu** |
| 5 | Choose an element, then an Aniimo | The Aniimo menu lists only that element, with pictures; the selection shows a big thumbnail and its element signs |
| 6 | Click **Lock in** | "🔒 You banned X". The old turn message disappears and a **new** one appears with the updated board. The board in `#live-draft` updates too |
| 7 | Try **🔍 Search by name** with `emb` (or part of any name) | A short list of matching names |
| 8 | Finish all 12 steps | Order: ban, ban, pick, pick, pick, pick, ban, ban, pick, pick, pick, pick. The same Aniimo can't be chosen twice. Then "draft is complete" and a panel with **Report result**, **Rematch**, **Call admin** |

Also check a dual-element Aniimo (for example **Inferlupa**, Fire/Dark): it appears under both elements, and on the
board both signs show.

**3.5 Rematch** (before reporting)

1. Click **Rematch → Replay same teams**, then **Accept**.
   **Expect** "Game 1 is replayed with the same teams (attempt 2)". The board is unchanged and the panel is back at **Report result**.
2. Click **Rematch → New draft**, then **Accept**.
   **Expect** the draft to restart at step 1. Redo it quickly (it's only for the test).

**3.6 Report**

1. Click **Report result** and choose a winner.
   **Expect** a "Result reported" panel with **Confirm** and **Dispute**, and "Auto-confirms in about a minute".
2. Click **Confirm**.
   **Expect** "🏆 Test X wins 1–0" with "Next: ... plays ... in the Final", and the thread archived after 2 minutes.
   There's **no Next game** button, because this is Bo1.

**3.7 Dispute** (in match 2)

Play it the same way, but after **Report result** click **Dispute**.
**Expect** a message in `#admin` with buttons for each player and **Replay game**. Click one.
**Expect** the match to finish with the result you picked.

**3.8 Round 2 and the final**

1. **Expect** in `#admin`: "Round 1 complete" with **Start Round 2**, and an updated bracket in `#announce`.
2. Click **Start Round 2**. **Expect** one new thread for the final, "Bo3".
3. Ready, side and draft as before, then play game 1 and confirm it.
   **Expect** "Game 1 confirmed", the score `1 – 0`, and a **Next game** button.
4. Click **Next game**.
   **Expect** the coin-flip message replaced by "loser's choice": the loser of game 1 picks the side. Play to two wins.
5. **Expect** in `#announce`: "🏆 ... wins Test Cup" with the final bracket and pick/ban stats.
6. Try `/leaderboard` and `/stats aniimo`. **Expect** ratings and pick/ban stats from the games you just played.
   (`/profile` needs a real server member, so try it in Test 2.)

**3.9 Fearless** (second tournament)

Create another tournament with **8 players, Quick, Fearless: on** and only two players (`11,1001,Test A` and
`22,1002,Test B`): the final is a Bo3. Play game 1, confirm it, then click **Next game**.
**Expect** on your first *pick* of game 2: "Fearless: N Aniimo you already played are locked for you", and those Aniimo are
missing from your menu. Your opponent can still pick them, and bans work as usual.

## 4. Test 2: two real accounts

This checks things dev mode skips. Use a second Discord account (a friend or an alt) that has joined the server.

1. Start a new tournament (`/tournament cancel` the old one if it's still open). Paste a CSV with the real IDs of both
   accounts (Developer Mode → right-click the user → **Copy User ID**):

   ```
   YOUR_ID,1001,Me
   OTHER_ID,1002,Other
   ```

2. Start it. **Expect** a private thread that **both of you** can see, with a ping for each of you.
   A non-admin member who isn't a player must **not** see the thread.
3. Each person clicks their own **Ready**. Try these on purpose:
   - The wrong person clicks **Take Side A**. **Expect** "The other player chooses the side".
   - The wrong person clicks **Make your choice**. **Expect** "It's X's turn to ban/pick".
   - An admin who isn't one of the two players clicks **Report result**. **Expect** "Only the players can report the result".
   - The reporter tries to click **Confirm** on their own report. **Expect** "Only the other player can confirm".
4. Draft once on your **phone**. **Expect** the menus, preview and **Lock in** to be usable at phone size.
5. Post a screenshot (any picture) in the thread while the result is being confirmed.
   **Expect** a 📎 reaction, and the picture appears on the "Result reported" panel.

## 5. Test 3: timers and admin tools

Use `Test A..D` fake players again (dev mode). Run `/admin` commands **inside the match thread**.

| Test | Do this | Expect |
|---|---|---|
| Draft timeout | Start a draft and don't click for 30 seconds | A grey "⏱️ ... ran out of time" line. A ban is skipped ("No ban"), a pick is random and tagged **AUTO** on the board |
| Side-choice timeout | Reach the coin flip and wait 30 seconds | "didn't choose in time ... is Side A" and the draft starts |
| No-show | Open a match and click nothing | After 1 min a reminder; after 2 min an alert in `#admin` with **Give win to ...**, **Extend 10 min**, **DQ both** |
| Auto-confirm | Report a result and wait 1 minute | "Game confirmed (auto-confirmed)" |
| Pause / resume | `/admin pause`, wait past the step time, then `/admin resume` | While paused the **Make your choice** button is greyed out, the board says "PAUSED BY AN ADMIN" and nothing times out. After resume the step gets a full timer |
| Undo | During a draft run `/admin undo` | The last ban or pick disappears from the board |
| Set result | In a disputed or playing game run `/admin set-result winner:` | The game is decided |
| Award | `/admin award winner:` on an unstarted match | The match ends as a walkover; ratings don't change |
| Replay | `/admin replay mode:same_teams` | A replay (attempt 2) with the same teams |
| DQ | `/admin dq player:` for a player in an open or upcoming match | The opponent advances (at once if the match is open, otherwise when their match comes up) |
| Swap | `/admin swap old: new: aniimo_uid:` on a player who hasn't started | The new name appears in the thread with a fresh UID card |
| Call admin | A player clicks **Call admin** | A ping in the thread and a line in `#admin` |

## 6. Test 4: restart in the middle of a draft

1. Start a draft and lock in two or three steps.
2. Stop the bot (Ctrl+C in its window) and start it again after about a minute.
3. **Expect** in the log: `Restarted N timers`. In the thread a **fresh turn message** with a full timer.
   The old buttons still work, and the draft carries on from the same step.

## 7. Test 5: a big bracket

1. Cancel the current tournament. Create one with **Players: Up to 32, Standard**. Generate 32 test players (paste them, or save as a
   `.csv` and attach it; 128 players need an attachment). On Windows PowerShell:
   `1..32 | % { "$($_*11),$(1000+$_),Test $_" }`; on macOS/Linux:
   `python3 -c "print('\n'.join(f'{i*11},{1000+i},Test {i}' for i in range(1,33)))"`
2. Confirm, then **Start**. **Expect** 16 threads in `#matches` within a minute, a bracket picture, and **no errors** in
   the log. (Rate limits show up as "429" or "rate limited" lines.)

## What only your server can confirm

Mark each as you go. These are the parts the automated tests couldn't check.

| ☐ | Check | If it fails |
|---|---|---|
| ☐ | Private threads are created and the right people are added (Test 2) | Give the bot **Create Private Threads** and **Send Messages in Threads** in `#matches` |
| ☐ | Admins see the threads | Give the admin role **Manage Threads** in `#matches` |
| ☐ | Pings reach players and the admin role | Check the role is mentionable, or the bot has **Mention All Roles** |
| ☐ | The CSV paste is read | Turn on **Message Content Intent** and paste as an admin in `#admin` |
| ☐ | Menus show Aniimo pictures and element signs | `/roster info` → *Emojis*. If it shows failures, read the log |
| ☐ | Buttons, menus and the picker work on a phone | Tell me which screen is awkward |
| ☐ | 16+ threads open without rate limit errors (Test 5) | Tell me; I'll slow the creation down |

## Troubleshooting

| Problem | Fix |
|---|---|
| `check` shows ✗ | Fill in the token or ID it names, then run `check` again |
| Slash commands missing | Check `guild_id`, invite with `applications.commands`, restart the bot, wait a minute |
| Nothing happens when I paste the CSV | Message Content Intent is off, or you're not an admin, or the active tournament is already past registration |
| "Only tournament admins can do that" | You need the `Tournament Admin` role (or the Administrator permission) |
| "... is still running. Finish or /tournament cancel it first" | Only one tournament runs at a time: finish it or use `/tournament cancel` |
| No Aniimo pictures | Run `run.bat fetch-assets`, then `/roster reload` |
| Clicking a button says "This interaction failed" | Look at `logs/bot.log`; send me the last 30 lines |

`logs/bot.log` has the details of every error. A message starting with ⚠️ means the bot is working and refused the action.

## Reset and going live

- **Start over:** stop the bot and delete `data/aniimo.db` (a backup is made each time the bot starts, in `data/backups/`).
- **Before a real event:** set `dev_mode: false`, restore the default `timers:` (delete the section), and restart.
  Real players must be members of the server; the CSV rejects anyone who isn't.
