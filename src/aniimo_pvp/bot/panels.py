"""Message builders (embeds + components), following the approved mockups."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..db.models import Match, Tournament
from ..domain import formats
from ..domain.series import series_winner
from ..domain.states import MatchState
from ..services.tournament import PlayersPreview
from ..services.views import MatchView
from .components import MatchButton, SetupSelect, TournamentButton, persistent_view
from .util import AMBER, GOLD, GREEN, PURPLE, RED, rel, truncate, who

if TYPE_CHECKING:
    from .client import AniimoBot

S = MatchState
B = discord.ButtonStyle


# ---------------- tournament setup ----------------
def rounds_table(fmt: str, size: int) -> str:
    plan = formats.plan(fmt, size)
    lines = [f"{'Round':<14}{'Players':>8}{'Matches':>9}  Series"]
    for r in plan:
        lines.append(f"{r.name:<14}{r.players:>8}{r.matches:>9}  Bo{r.bo}")
    return "```\n" + "\n".join(lines) + "\n```"


def setup_embed(t: Tournament) -> discord.Embed:
    fmt = formats.FORMATS[t.format]
    step = "STEP 1 OF 2 · FORMAT" if t.status == "setup" else "FORMAT (can change until Start)"
    e = discord.Embed(title=f"New tournament · {t.name}", colour=PURPLE)
    e.set_author(name=step)
    e.description = (
        f"Single elimination · **{fmt.label}** · Fearless {'on' if t.fearless else 'off'}\n"
        f"{rounds_table(t.format, t.player_cap)}"
        f"**{formats.round_count(t.player_cap)}** rounds · **{t.player_cap - 1}** matches · "
        f"up to **{formats.max_games(t.format, t.player_cap)}** games\n"
        "-# Fewer players than the cap is fine: the bracket shrinks to the next size and byes fill the gaps. "
        "Series lengths are counted back from the final."
    )
    return e


def setup_view(t: Tournament) -> discord.ui.View:
    locked = t.status not in ("setup", "registration")
    caps = [
        discord.SelectOption(
            label=f"Up to {c} players",
            value=str(c),
            default=c == t.player_cap,
            description=f"{formats.round_count(c)} rounds · {c - 1} matches",
        )
        for c in formats.CAPS
    ]
    fmts = [
        discord.SelectOption(
            label=f.label + (" (recommended)" if k == "standard" else ""),
            value=k,
            description=f.description,
            default=k == t.format,
        )
        for k, f in formats.FORMATS.items()
    ]
    fear = [
        discord.SelectOption(
            label="Fearless: off",
            value="off",
            default=not t.fearless,
            description="Every Aniimo is available in every game.",
        ),
        discord.SelectOption(
            label="Fearless: on",
            value="on",
            default=t.fearless,
            description="In a series, you can't re-pick an Aniimo you already played.",
        ),
    ]
    items = [
        SetupSelect("cap", t.id, caps, "Players", disabled=locked),
        SetupSelect("fmt", t.id, fmts, "Format", disabled=locked),
        SetupSelect("fear", t.id, fear, "Fearless", disabled=locked),
    ]
    if t.status == "setup":
        items.append(TournamentButton("next", t.id, label="Next: add players", style=B.success, emoji="➡️"))
    items.append(TournamentButton("rename", t.id, label="Rename", disabled=locked))
    items.append(TournamentButton("cancel", t.id, label="Cancel", disabled=t.status in ("finished", "cancelled")))
    return persistent_view(*items)


def step2_embed(t: Tournament) -> discord.Embed:
    fmt = formats.FORMATS[t.format]
    e = discord.Embed(title=f"{t.name} · add players", colour=PURPLE)
    e.set_author(name="STEP 2 OF 2 · PLAYERS")
    e.add_field(name="Format", value=fmt.label)
    e.add_field(name="Player cap", value=f"Up to {t.player_cap}")
    e.add_field(name="Fearless", value="On" if t.fearless else "Off")
    e.description = (
        f"Paste the player list here as CSV text, or attach a .csv file (up to {t.player_cap} players).\n"
        "Columns: `discord_id, aniimo_uid, display_name` (display name optional)."
    )
    return e


def preview_embed(t: Tournament, p: PlayersPreview) -> discord.Embed:
    ok = p.can_confirm and not p.issues
    e = discord.Embed(title="Player list preview", colour=GREEN if ok else AMBER if p.can_confirm else RED)
    e.add_field(name="Valid players", value=str(len(p.rows)))
    e.add_field(
        name="Bracket",
        value=f"{p.bracket_size} slots · {p.byes} bye{'s' if p.byes != 1 else ''}" if p.bracket_size else "—",
    )
    e.add_field(name="Skipped", value=f"{len(p.issues)} row{'s' if len(p.issues) != 1 else ''}")
    if p.rounds:
        e.add_field(name="Rounds", value=" · ".join(f"{r.name} Bo{r.bo}" for r in p.rounds), inline=False)
    if p.issues:
        lines = [f"Row {i.line}: {i.message}" for i in p.issues[:15]]
        if len(p.issues) > 15:
            lines.append(f"…and {len(p.issues) - 15} more")
        e.add_field(name="Problems", value=truncate("\n".join(lines), 1024), inline=False)
    if p.blocking:
        e.add_field(name="Can't confirm yet", value=truncate("\n".join(p.blocking), 1024), inline=False)
    fakes = sum(1 for r in p.rows if r.fake)
    footer = "Paste a fixed CSV to replace this list."
    if fakes:
        footer += f" Dev mode: {fakes} test player(s) who aren't server members."
    e.set_footer(text=footer)
    return e


def preview_view(t: Tournament, p: PlayersPreview) -> discord.ui.View:
    return persistent_view(
        TournamentButton(
            "confirm",
            t.id,
            label=f"Confirm {len(p.rows)} players",
            style=B.success,
            emoji="✅",
            disabled=not p.can_confirm,
        ),
        TournamentButton("discard", t.id, label="Cancel", emoji="✖️"),
    )


def confirmed_embed(t: Tournament, count: int) -> discord.Embed:
    e = discord.Embed(
        title=f"{count} players confirmed",
        colour=GREEN,
        description=f"Click **Start tournament** to shuffle the bracket and open the first round. "
        f"Format: {formats.FORMATS[t.format].label}.",
    )
    return e


def start_view(t: Tournament) -> discord.ui.View:
    return persistent_view(TournamentButton("start", t.id, label="Start tournament", style=B.success, emoji="▶️"))


# ---------------- match room ----------------
def uid_card(v: MatchView, bot: AniimoBot) -> tuple[str, discord.Embed]:
    content = f"{who(v.p1)} {who(v.p2)} your {v.round_name} match is ready."
    if bot.settings.admin_role_id:
        content += f" ({'<@&' + str(bot.settings.admin_role_id) + '>'} can see this thread.)"
    e = discord.Embed(title=f"{v.label} · {v.p1.name} vs {v.p2.name} · Bo{v.match.bo}", colour=PURPLE)
    e.add_field(name=f"{v.p1.name} · player 1 · hosts", value=f"Aniimo UID\n```{v.p1.uid}```")
    e.add_field(name=f"{v.p2.name} · player 2", value=f"Aniimo UID\n```{v.p2.uid}```")
    e.description = (
        f"{v.p1.name} creates the in-game room and invites {v.p2.name} by UID. Click **Ready** when you're both online."
    )
    return content, e


def _ready_line(name: str, ready: bool) -> str:
    return f"{'✅' if ready else '⬜'} {name} — {'Ready' if ready else 'Not ready'}"


def panel(v: MatchView, bot: AniimoBot) -> tuple[str | None, discord.Embed, discord.ui.View | None]:
    """The control panel for the current stage: (ping content, embed, buttons)."""
    m = v.match
    mid = m.id
    st = m.state
    g = v.game
    call_admin = MatchButton("admin", mid, label="Call admin", emoji="🆘")
    if st in (S.WAITING, S.NOSHOW):
        e = discord.Embed(title="Ready check", colour=PURPLE)
        e.description = "\n".join([_ready_line(v.p1.name, m.p1_ready), _ready_line(v.p2.name, m.p2_ready)])
        if st == S.NOSHOW:
            e.add_field(name="Waiting for an admin", value="The ready check timed out. An admin will decide.")
        view = persistent_view(MatchButton("ready", mid, label="Ready", style=B.success, emoji="✅"), call_admin)
        missing = [p for p, r in ((v.p1, m.p1_ready), (v.p2, m.p2_ready)) if not r]
        return " ".join(who(p) for p in missing) or None, e, view
    if st == S.SIDE_CHOICE:
        chooser = v.player(m.side_chooser_id)
        if m.coin_winner_id:
            title = f"🪙 {chooser.name} wins the toss"
            desc = f"Coin flip for Game {g.number}. {who(chooser)}, choose your side."
        else:
            loser_note = f"{chooser.name} lost the last game, so {chooser.name} chooses the side for Game {g.number}."
            title, desc = f"Game {g.number} · loser's choice", loser_note
        e = discord.Embed(title=title, colour=GOLD, description=desc)
        e.add_field(name="🟦 Side A · first pick", value="First ban and first pick.\nPicks #1, #4, #6, #7.")
        e.add_field(
            name="🟧 Side B · counter-pick", value="First ban of phase 2 and the last pick.\nPicks #2, #3, #5, #8."
        )
        deadline = v.due.get("side_choice")
        e.add_field(name="Time", value=f"No answer {rel(deadline)} means Side A." if deadline else "—", inline=False)
        if not v.tournament.fearless and g.number > 1:
            e.set_footer(text="Fearless is off, so every Aniimo is available again.")
        elif v.tournament.fearless and g.number > 1:
            e.set_footer(text="Fearless is on: you can't re-pick an Aniimo you played earlier in this series.")
        view = persistent_view(
            MatchButton("side", mid, "A", label="Take Side A · first pick", style=B.primary),
            MatchButton("side", mid, "B", label="Take Side B · counter-pick", style=B.secondary),
        )
        return who(chooser), e, view
    if st in (S.DRAFTING, S.PAUSED):
        e = discord.Embed(
            title=f"Drafting Game {g.number}" + (" (paused)" if st == S.PAUSED else ""),
            colour=PURPLE,
            description="Follow the turn message below. The active player clicks **Make your choice**.",
        )
        return None, e, persistent_view(call_admin)
    if st == S.PLAYING:
        host, guest = v.p1, v.p2
        replay = f" (replay, attempt {g.attempt})" if g.attempt > 1 else ""
        e = discord.Embed(title=f"Play Game {g.number} in Aniimo{replay}", colour=GREEN)
        e.description = (
            f"{host.name} hosts the room (`{host.uid}`) and invites {guest.name} (`{guest.uid}`). "
            "Use only the 4 Aniimo you drafted.\nWhen the game ends, either player clicks **Report result**."
        )
        items = [
            MatchButton("report", mid, label="Report result", style=B.success, emoji="🏆"),
            MatchButton("rematch", mid, label="Rematch", emoji="🔁"),
            call_admin,
        ]
        ping = None
        if m.rematch_by:
            asker = v.player(m.rematch_by)
            other = v.opponent(m.rematch_by)
            what = "replay with the same teams" if m.rematch_mode == "same_teams" else "redo the draft"
            e.add_field(
                name=f"🔁 Rematch requested by {asker.name}",
                value=f"{asker.name} wants to **{what}** for Game {g.number}. "
                f"The series score doesn't change and this attempt is saved as voided. "
                f"{other.name}, accept?",
                inline=False,
            )
            items = [
                MatchButton("rematch_yes", mid, label="Accept", style=B.success, emoji="✅"),
                MatchButton("rematch_no", mid, label="Decline", style=B.danger, emoji="✖️"),
                call_admin,
            ]
            ping = who(other)
        return ping, e, persistent_view(*items)
    if st == S.CONFIRMING:
        reporter = v.player(g.reported_by)
        winner = v.player(g.reported_winner_id)
        other = v.opponent(g.reported_by)
        e = discord.Embed(title=f"Result reported · Game {g.number}", colour=AMBER)
        e.description = (
            f"{reporter.name} reported: **{winner.name} won**.\n"
            f"{other.name}, please confirm. Auto-confirms {rel(v.due.get('auto_confirm'))} if nobody disputes."
        )
        if g.screenshot_url:
            e.set_thumbnail(url=g.screenshot_url)
        view = persistent_view(
            MatchButton("confirm", mid, label="Confirm", style=B.success, emoji="✅"),
            MatchButton("dispute", mid, label="Dispute", style=B.danger, emoji="🚩"),
        )
        return who(other), e, view
    if st == S.DISPUTED:
        e = discord.Embed(
            title=f"Disputed · Game {g.number}",
            colour=RED,
            description="An admin has been notified and will set the result or replay the game.",
        )
        return None, e, persistent_view(call_admin)
    if st == S.BETWEEN_GAMES:
        chooser_id = None
        conf = v.confirmed_games()
        if conf:
            last = conf[-1]
            chooser_id = m.p2_id if last.winner_id == m.p1_id else m.p1_id
        chooser = v.player(chooser_id)
        last_winner = v.player(conf[-1].winner_id) if conf else None
        e = discord.Embed(
            title=f"Game {conf[-1].number if conf else ''} confirmed · {last_winner.name if last_winner else ''} wins",
            colour=GREEN,
        )
        e.description = (
            f"**{v.p1.name}** {_pips(v.score.p1, v.wins_needed)}  **{v.score.p1} – {v.score.p2}**  "
            f"{_pips(v.score.p2, v.wins_needed)} **{v.p2.name}**\n"
            f"Best of {m.bo}: first to {v.wins_needed} wins. "
            f"{chooser.name if chooser else 'The loser'} lost, so they choose the side for the next game."
        )
        view = persistent_view(MatchButton("next", mid, label="Next game", style=B.primary, emoji="▶️"), call_admin)
        return None, e, view
    if st == S.DONE:
        winner = v.player(m.winner_id)
        e = discord.Embed(title=f"Match over · {winner.name if winner else 'no winner'}", colour=GOLD)
        return None, e, None
    e = discord.Embed(title="Waiting", colour=PURPLE, description="This match opens when its round starts.")
    return None, e, None


def _pips(n: int, need: int) -> str:
    return "●" * n + "○" * max(0, need - n)


def turn_message(v: MatchView, bot: AniimoBot) -> tuple[str, discord.ui.View | None]:
    g = v.game
    t = v.turn
    if t is None:
        return f"{who(v.p1)} {who(v.p2)} the Game {g.number} draft is complete. Good luck!", None
    side, kind = t
    player = v.turn_player
    paused = v.match.state == S.PAUSED
    deadline = f" · ends {rel(v.step_deadline)}" if v.step_deadline and not paused else ""
    pause_note = " · ⏸️ paused by an admin" if paused else ""
    content = (
        f"{who(player)} it's your **{kind.upper()}** · Step {v.draft.step + 1} of {len(v.order)} · "
        f"{v.phase}{deadline}{pause_note}"
    )
    view = persistent_view(
        MatchButton(
            "pick",
            v.match.id,
            f"{g.id}-{v.draft.step}",
            label="Make your choice",
            style=B.primary,
            emoji="🎯",
            disabled=paused,
        ),
        MatchButton("admin", v.match.id, label="Call admin", emoji="🆘"),
    )
    return content, view


def match_won_embed(v: MatchView, info: dict) -> tuple[str, discord.Embed]:
    m = v.match
    winner, loser = v.player(m.winner_id), v.opponent(m.winner_id)
    if winner is None:
        e = discord.Embed(title="Match over · both players disqualified", colour=RED)
        return "", e
    sc_w, sc_l = v.score_of(winner.id), v.score_of(loser.id) if loser else 0
    title = f"🏆 {winner.name} wins {sc_w}–{sc_l}" if not m.walkover else f"🏆 {winner.name} advances (walkover)"
    e = discord.Embed(title=title, colour=GOLD)
    lines = []
    for g in v.games:
        if g.status == "voided":
            continue
        gw = v.player(g.winner_id)
        a, b = v.player(g.side_a_id), v.player(g.side_b_id)
        replays = sum(1 for x in v.games if x.number == g.number and x.status == "voided")
        extra = f" · replayed {replays}×" if replays else ""
        if gw:
            lines.append(
                f"**Game {g.number}** — {gw.name}  ·  {a.name if a else '?'} A · {b.name if b else '?'} B{extra}"
            )
    if lines:
        e.description = "\n".join(lines)
    nxt = info.get("next_label")
    if nxt:
        e.add_field(name="Next", value=nxt, inline=False)
    if m.p1_rating_delta is not None and v.p1 and v.p2:

        def fmt(p, delta):
            return f"{p.name} {p.rating} ({'+' if delta >= 0 else ''}{delta})"

        e.add_field(
            name="Rating", value=f"{fmt(v.p1, m.p1_rating_delta)} · {fmt(v.p2, m.p2_rating_delta)}", inline=False
        )
    e.set_footer(text="This thread is archived in a few minutes. Draft boards stay in the live feed.")
    content = f"{who(winner)} wins {v.label}." + (f" Well played, {who(loser)}." if loser else "")
    return content, e


# ---------------- announcements ----------------
def round_table_embeds(t: Tournament, round_no: int, rows: list[tuple[Match, object, object]]) -> list[discord.Embed]:
    size = t.bracket_size or 2
    players = size >> (round_no - 1)
    bo = formats.bo_for_round(t.format, size, round_no)
    lines = []
    for m, p1, p2 in rows:
        label = f"`M{m.slot + 1:<3}`"
        if m.is_bye:
            name = (p1 or p2).name if (p1 or p2) else "?"
            lines.append(f"{label} {name} — bye, advances")
        elif m.state == "done" and m.walkover:
            w = p1 if p1 and p1.id == m.winner_id else p2
            lines.append(f"{label} {who(w, mention=False)} advances (walkover)")
        else:
            lines.append(f"{label} {who(p1)} vs {who(p2)}")
    embeds = []
    for i in range(0, len(lines), 32):
        e = discord.Embed(colour=PURPLE, description="\n".join(lines[i : i + 32]))
        if i == 0:
            e.title = f"Round {round_no} · {formats.round_name(players)} · Bo{bo}"
        embeds.append(e)
    embeds[-1].set_footer(text="Your match thread is open in the matches channel. Click Ready when you're online.")
    return embeds[:10]


def round_complete_embed(t: Tournament, round_no: int) -> discord.Embed:
    size = t.bracket_size or 2
    nxt = formats.round_name(size >> round_no)
    return discord.Embed(
        title=f"Round {round_no} complete",
        colour=GREEN,
        description=f"All matches are confirmed and the updated bracket is in the announce channel.\n"
        f"Next: **{nxt}** (Bo{formats.bo_for_round(t.format, size, round_no + 1)}).",
    )


def next_round_view(t: Tournament, round_no: int) -> discord.ui.View:
    return persistent_view(
        TournamentButton("nextround", t.id, str(round_no), label=f"Start Round {round_no}", style=B.primary, emoji="▶️")
    )


def admin_noshow(v: MatchView, missing: list[int], bot: AniimoBot) -> tuple[discord.Embed, discord.ui.View]:
    m = v.match
    e = discord.Embed(title=f"No-show · {v.label} · {v.p1.name} vs {v.p2.name}", colour=AMBER)
    e.description = (
        f"{v.p1.name} {'Ready ✅' if m.p1_ready else 'Not ready ❌'} · "
        f"{v.p2.name} {'Ready ✅' if m.p2_ready else 'Not ready ❌'}\n"
        f"The ready check timed out. Opened {rel(m.opened_at)}."
    )
    if m.thread_id:
        e.add_field(name="Thread", value=f"<#{m.thread_id}>")
    view = persistent_view(
        MatchButton("award", m.id, str(m.p1_id), label=f"Give win to {truncate(v.p1.name, 30)}", style=B.success),
        MatchButton("award", m.id, str(m.p2_id), label=f"Give win to {truncate(v.p2.name, 30)}", style=B.success),
        MatchButton("extend", m.id, label=f"Extend {int(bot.settings.timers.noshow_extend_minutes)} min"),
        MatchButton("dqboth", m.id, label="DQ both", style=B.danger),
    )
    return e, view


def admin_dispute(v: MatchView) -> tuple[discord.Embed, discord.ui.View]:
    m, g = v.match, v.game
    reporter, winner, other = v.player(g.reported_by), v.player(g.reported_winner_id), v.opponent(g.reported_by)
    e = discord.Embed(title=f"Dispute · {v.label} · {v.p1.name} vs {v.p2.name} · Game {g.number}", colour=RED)
    e.description = (
        f"{reporter.name} reported **{winner.name} won**. {other.name} disputed.\n"
        f"Series so far: {v.p1.name} {v.score.p1}–{v.score.p2} {v.p2.name}."
    )
    if g.screenshot_url:
        e.set_thumbnail(url=g.screenshot_url)
    if m.thread_id:
        e.add_field(name="Thread", value=f"<#{m.thread_id}>")
    view = persistent_view(
        MatchButton("setres", m.id, str(m.p1_id), label=f"{truncate(v.p1.name, 30)} won", style=B.primary),
        MatchButton("setres", m.id, str(m.p2_id), label=f"{truncate(v.p2.name, 30)} won", style=B.primary),
        MatchButton("replay", m.id, "same_teams", label="Replay game"),
    )
    return e, view


def live_status_embed(t: Tournament, rows: list[tuple[Match, object, object]]) -> discord.Embed:
    size = t.bracket_size or 2
    players = size >> (t.current_round - 1) if t.current_round else size
    e = discord.Embed(title=f"{t.name} · Round {t.current_round} now ({formats.round_name(players)})", colour=PURPLE)
    labels = {
        "pending": "⏳ Not started",
        "waiting": "🟡 Waiting for players",
        "noshow": "🟠 No-show (admin)",
        "side_choice": "🪙 Choosing sides",
        "drafting": "🔴 Drafting",
        "paused": "⏸️ Paused",
        "playing": "🎮 Playing",
        "confirming": "📝 Confirming result",
        "disputed": "🚩 Disputed",
        "between_games": "🎮 Between games",
        "done": "✅ Done",
    }
    lines = []
    for m, p1, p2 in rows:
        if m.is_bye:
            lines.append(f"`M{m.slot + 1:<3}` {(p1 or p2).name if (p1 or p2) else '?'} · bye")
            continue
        n1 = p1.name if p1 else "TBD"
        n2 = p2.name if p2 else "TBD"
        lines.append(f"`M{m.slot + 1:<3}` {n1} vs {n2} · {labels.get(m.state, m.state)}")
    e.description = truncate("\n".join(lines) or "No matches yet", 4000)
    return e


def champion_embed(t: Tournament, champion, summary: dict, roster_name) -> discord.Embed:
    e = discord.Embed(title=f"🏆 {champion.name if champion else 'No champion'} wins {t.name}!", colour=GOLD)
    if summary.get("most_banned"):
        e.add_field(name="Most banned", value="\n".join(f"{roster_name(s)} · {n}" for s, n in summary["most_banned"]))
    if summary.get("most_picked"):
        e.add_field(name="Most picked", value="\n".join(f"{roster_name(s)} · {n}" for s, n in summary["most_picked"]))
    if summary.get("best_win_rate"):
        e.add_field(
            name="Best win rate (3+ picks)",
            value="\n".join(f"{roster_name(s)} · {n}%" for s, n in summary["best_win_rate"]),
        )
    games = summary.get("drafts", [("games", 0)])[0][1]
    e.set_footer(text=f"{games} games played · Format {formats.FORMATS[t.format].label}")
    return e


def series_text(v: MatchView) -> str:
    w = series_winner(v.score, v.match.bo, v.match.p1_id, v.match.p2_id) if v.p1 and v.p2 else None
    return f"{v.p1.name} {v.score.p1}–{v.score.p2} {v.p2.name}" + (" (decided)" if w else "")
