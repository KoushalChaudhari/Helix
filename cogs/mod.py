from __future__ import annotations

import contextlib
from email.mime import message
import discord
import asyncio
import re
import uuid

from datetime import datetime, timezone, timedelta  
from typing import Optional
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from db.engine import AsyncSessionLocal
from db.models import GuildConfig
from config import PREFIX


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: per-guild config (stored compactly in GuildConfig.modules JSON)
# ──────────────────────────────────────────────────────────────────────────────
async def get_guild_cfg(session, guild_id: str) -> GuildConfig:
    """Fetch-or-create the GuildConfig row for this guild."""
    res = await session.execute(
        select(GuildConfig).where(GuildConfig.guild_id == guild_id)
    )
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = GuildConfig(
            id=uuid.uuid4().hex,
            guild_id=guild_id,
            prefix=";",
            modules={},  # we store tiny state here (modlog channel, counters, index)
        )
        session.add(cfg)
        await session.commit()
    if cfg.modules is None:
        cfg.modules = {}
    return cfg


def get_modlog_channel_id(cfg: GuildConfig) -> Optional[int]:
    raw = (cfg.modules or {}).get("modlog_channel_id")
    return int(raw) if raw else None


def set_modlog_channel_id(cfg: GuildConfig, channel_id: int) -> None:
    mods = cfg.modules or {}
    mods["modlog_channel_id"] = str(channel_id)
    cfg.modules = mods
    flag_modified(cfg, "modules")  # ensure JSON mutation is persisted


def next_case_number(cfg: GuildConfig) -> int:
    mods = cfg.modules or {}
    n = int(mods.get("case_seq", 0)) + 1
    mods["case_seq"] = str(n)
    cfg.modules = mods
    flag_modified(cfg, "modules")  # ensure JSON mutation is persisted
    return n


def _get_case_index(cfg: GuildConfig) -> dict:
    """Return the per-guild {case_no: {c: channel_id, m: message_id}} map."""
    mods = cfg.modules or {}
    idx = mods.get("case_index") or {}
    return idx if isinstance(idx, dict) else {}

def _set_case_index_entry(cfg: GuildConfig, case_no: int, channel_id: int, message_id: int, user_id: int | None = None) -> None:
    mods = cfg.modules or {}
    idx = mods.get("case_index") or {}
    entry = {"c": str(channel_id), "m": str(message_id)}
    if user_id is not None:
        entry["u"] = str(user_id)  
    idx[str(case_no)] = entry
    mods["case_index"] = idx
    cfg.modules = mods
    flag_modified(cfg, "modules")


def _find_field(embed: discord.Embed, name: str) -> Optional[int]:
    for i, f in enumerate(embed.fields):
        if f.name.lower() == name.lower():
            return i
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Time helpers
# ──────────────────────────────────────────────────────────────────────────────
_UNIT_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def parse_duration_ms(s: str) -> Optional[int]:
    """
    Parse "1h30m", "45m", "2d", "90" (seconds) → milliseconds.
    Returns None on invalid input.
    """
    if not s:
        return None
    s = s.strip().lower()
    total = 0
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
            continue
        if ch in _UNIT_MS and num:
            total += int(num) * _UNIT_MS[ch]
            num = ""
        elif ch.isspace():
            continue
        else:
            return None
    if num:
        total += int(num) * _UNIT_MS["s"]
    return total or None


def humanize_ms(ms: int) -> str:
    parts = []
    for unit, size in (("w", 604800000), ("d", 86400000), ("h", 3600000), ("m", 60000), ("s", 1000)):
        if ms >= size:
            n, ms = divmod(ms, size)
            parts.append(f"{n}{unit}")
    return "".join(parts) or "0s"



# ====== Standard colors for all moderation responses ======
COLORS = {
    "INFO": discord.Color.blurple(),
    "SUCCESS": discord.Color.green(),
    "WARNING": discord.Color.gold(),
    "ERROR": discord.Color.red(),

    # action colors (used by _log_case and action summaries)
    "WARN": discord.Color.gold(),
    "MUTE": discord.Color.orange(),
    "UNMUTE": discord.Color.green(),
    "KICK": discord.Color.red(),
    "BAN": discord.Color.dark_red(),
    "UNBAN": discord.Color.green(),
}

def mkembed(title: str, desc: str | None = None, *, color: discord.Color | None = None) -> discord.Embed:
    """Create a consistent embed with timestamp."""
    return discord.Embed(
        title=title,
        description=desc or "",
        color=color or COLORS["INFO"],
        timestamp=datetime.now(timezone.utc),
    )

async def send_info(ctx, title, desc=""):   return await ctx.send(embed=mkembed(title, desc, color=COLORS["INFO"]))
async def send_ok(ctx, title, desc=""):     return await ctx.send(embed=mkembed(title, desc, color=COLORS["SUCCESS"]))
async def send_warn(ctx, title, desc=""):   return await ctx.send(embed=mkembed(title, desc, color=COLORS["WARNING"]))
async def send_err(ctx, title, desc=""):    return await ctx.send(embed=mkembed(title, desc, color=COLORS["ERROR"]))



# ──────────────────────────────────────────────────────────────────────────────
# Moderation Cog
# ──────────────────────────────────────────────────────────────────────────────
class Moderation(commands.Cog):
    """Moderation: warn + mod-log config + edit logged cases (no case DB rows)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_case(
        self,
        ctx: commands.Context,
        target: discord.abc.User,
        action: str,                  
        reason: str,
        duration: str | None,
        dm_ok: bool,
    ):
        """Create a per-guild case number, post the log embed, and index the message location + user_id."""
        # 1) get next case number + mod-log id
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            case_no = next_case_number(cfg)
            modlog_id = get_modlog_channel_id(cfg)
            session.add(cfg)
            await session.commit()

        # 2) build the log embed (use your standardized colors)
        action_color = {
            "Warn": COLORS["WARN"],
            "Mute": COLORS["MUTE"],
            "Timeout": COLORS["MUTE"],
            "Unmute": COLORS["UNMUTE"],
            "Kick": COLORS["KICK"],
            "Ban": COLORS["BAN"],
            "Unban": COLORS["UNBAN"],
        }.get(action, COLORS["INFO"])

        embed = discord.Embed(
            color=action_color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f"Case {case_no} | {action} | {getattr(target, 'name', str(target))}",
            icon_url=(target.display_avatar.url if getattr(target, "display_avatar", None) else discord.Embed.Empty),
        )
        embed.add_field(name="User", value=f"{getattr(target, 'mention', str(target))} (`{getattr(target, 'id', '')}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=(reason[:1024] or "No reason provided"), inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)

        # 3) resolve log channel and send
        channel = None
        if modlog_id:
            channel = ctx.guild.get_channel(modlog_id) or self.bot.get_channel(modlog_id)
            if channel is None:
                try:
                    channel = await ctx.guild.fetch_channel(modlog_id)
                except Exception:
                    channel = None

        message = await (channel or ctx.channel).send(embed=embed)

        # 4) index this case → (channel, message, user)
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            _set_case_index_entry(cfg, case_no, message.channel.id, message.id, getattr(target, "id", None))
            session.add(cfg)
            await session.commit()

        # 5) short summary back to the invoker (embed)
        past_map = {"Warn": "warned", "Mute": "muted", "Timeout": "timed out", "Unmute": "unmuted", "Kick": "kicked", "Ban": "banned", "Unban": "unbanned"}
        past = past_map.get(action, action.lower() + "ed")
        note = "DM sent." if dm_ok else "DM failed."
        summary = mkembed(
            title=f"{getattr(target, 'name', str(target))} was {past}",
            desc=(f"Reason: {reason}" + (f"\nDuration: {duration}" if duration else f"")) + f"\n{note}",
            color=action_color,
        )
        summary.set_footer(text=f"Case {case_no} • Moderator: {ctx.author}")
        await ctx.send(embed=summary)



# =============================================
#              CONFIGURE MOD-LOG
# =============================================
    @commands.command(name="modlog")
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True)
    async def modlog(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))

            if channel is None:
                cid = get_modlog_channel_id(cfg)
                if not cid:
                    return await send_info(ctx, "Mod-log", "Current mod-log channel: **not set**")
                ch = ctx.guild.get_channel(cid) or self.bot.get_channel(cid)
                if ch is None:
                    try:
                        ch = await ctx.guild.fetch_channel(cid)  # type: ignore
                    except Exception:
                        ch = None
                return await send_info(
                    ctx, "Mod-log",
                    f"Current mod-log channel: {ch.mention if ch else f'`{cid}` (not accessible)'}"
                )

            set_modlog_channel_id(cfg, channel.id)
            session.add(cfg)
            await session.commit()

        await send_ok(ctx, "Mod-log", f"Mod-log channel set to {channel.mention}")





# =============================================
#                    WARN
# =============================================
    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, member: discord.Member, *, reason="No reason provided"):
        """Warn a user and store the warning."""
        if member.bot or member == ctx.author:
            return await ctx.reply("Invalid target.")

        # DM first
        dm_ok = True
        try:
            await member.send(f"You were **warned** in **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        # Store the warning in GuildConfig.modules JSON
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            cfg.modules = cfg.modules or {}
            warns = cfg.modules.get("warns", {})

            # append new warning
            user_warns = warns.get(str(member.id), [])
            user_warns.append({
                "reason": reason,
                "moderator": str(ctx.author.id),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            warns[str(member.id)] = user_warns
            cfg.modules["warns"] = warns

            flag_modified(cfg, "modules")  # 👈 critical for nested JSON
            session.add(cfg)
            await session.commit()

        await self._log_case(ctx, member, "Warn", reason, None, dm_ok)
        # _log_case sends the action embed to the invoking channel; no additional reply needed.




# =============================================
#                    WARNS
# =============================================
    @commands.command(name="warns", aliases=["warnings"])
    async def warns(self, ctx, member: Optional[discord.Member] = None):
        """Display a user's stored warnings."""
        member = member or ctx.author
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            cfg.modules = cfg.modules or {}
            warns = cfg.modules.get("warns", {})
            user_warns = warns.get(str(member.id), [])

        if not user_warns:
            return await ctx.reply(f"{member.mention} has no warnings.")

        embed = discord.Embed(
            title=f"Warnings for {member}",
            color=COLORS["WARNING"],
            timestamp=datetime.now(timezone.utc),
        )

        for i, w in enumerate(user_warns, 1):
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
            embed.add_field(
                name=f"{i}. {w['reason']}",
                value=f"Moderator: <@{w['moderator']}> • {ts}",
                inline=False,
            )

        await ctx.reply(embed=embed)






# =============================================
#                 CLEARWARNS
# =============================================
    @commands.command(name="clearwarns", aliases=["clearwarnings"])
    @commands.has_permissions(manage_messages=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            mods = cfg.modules or {}
            warns_map = mods.get("warns", {})
            if str(member.id) in warns_map:
                warns_map.pop(str(member.id))
                mods["warns"] = warns_map
                cfg.modules = mods
                flag_modified(cfg, "modules")
                session.add(cfg)
                await session.commit()
                return await send_ok(ctx, "Clear Warnings", f"Cleared all warnings for {member.mention}.")
        await send_info(ctx, "Clear Warnings", f"{member.mention} has no warnings.")





# =============================================
#                    MUTE
# =============================================
    @commands.command()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True, send_messages=True, embed_links=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        """Timeout (mute) a member for a given duration (e.g., 30m, 2h, 1d)."""
        if member.bot:
            return await ctx.reply("You can’t mute bots.")
        if member == ctx.author:
            return await ctx.reply("You can’t mute yourself.")

        ms = parse_duration_ms(duration)
        if ms is None or ms <= 0:
            return await ctx.reply("Invalid duration. Try `10m`, `2h`, `1d`, `1h30m`.")

        # Discord timeout max ~28 days
        max_ms = 28 * 24 * 60 * 60 * 1000
        if ms > max_ms:
            return await ctx.reply("Duration too long. Max is **28d**.")

        # DM first
        dm_ok = True
        try:
            await member.send(
                f"You have been **muted** in **{ctx.guild.name}** for **{duration}**.\n"
                f"**Reason:** {reason}"
            )
        except Exception:
            dm_ok = False

        try:
            until = datetime.now(timezone.utc) + timedelta(milliseconds=ms)
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            return await ctx.reply("I don’t have permission to mute that member.")
        except Exception as e:
            return await ctx.reply(f"Error muting member: {e}")

        await self._log_case(ctx, member, "Mute", reason, duration, dm_ok)





# =============================================
#                     UNMUTE
# =============================================
    @commands.command()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True, send_messages=True, embed_links=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Remove timeout (mute) from a member."""
        dm_ok = True
        try:
            await member.send(
                f"You have been **unmuted** in **{ctx.guild.name}**.\n"
                f"**Reason:** {reason}"
            )
        except Exception:
            dm_ok = False

        try:
            await member.timeout(None, reason=reason)  # clears timeout
        except discord.Forbidden:
            return await ctx.reply("I don’t have permission to unmute that member.")
        except Exception as e:
            return await ctx.reply(f"Error unmuting member: {e}")

        await self._log_case(ctx, member, "Unmute", reason, None, dm_ok)





# =============================================
#                    KICK
# =============================================
    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True, send_messages=True, embed_links=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Kick a member (DMs them before kicking)."""
        if member == ctx.author:
            return await ctx.reply("You can’t kick yourself.")
        if member.bot:
            return await ctx.reply("You can’t kick bots.")

        # Try DM first
        dm_ok = True
        try:
            await member.send(
                f"You have been **kicked** from **{ctx.guild.name}**.\n"
                f"**Reason:** {reason}"
            )
        except Exception:
            dm_ok = False

        # Now perform the kick
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await ctx.reply("I don’t have permission to kick that member.")
        except Exception as e:
            return await ctx.reply(f"Error kicking member: {e}")

        # Log the action
        await self._log_case(ctx, member, "Kick", reason, None, dm_ok)





# =============================================
#                   BAN
# =============================================
    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True, send_messages=True, embed_links=True)
    async def ban(self, ctx: commands.Context, target: discord.User, *, reason: str = "No reason provided"):
        """Ban a user (DM first, then ban). Accepts mention or user ID."""
        # prevent self-ban or bot-ban edge cases
        if isinstance(target, discord.Member) and target == ctx.author:
            return await ctx.reply("You can’t ban yourself.")
        if target.bot:
            return await ctx.reply("You can’t ban bots.")

        # Try DM before the ban (most reliable while you still share a server)
        dm_ok = True
        try:
            await target.send(
                f"You have been **banned** from **{ctx.guild.name}**.\n"
                f"**Reason:** {reason}"
            )
        except Exception:
            dm_ok = False

        # Perform the ban
        try:
            # Works for both Members and Users (if already left the guild)
            await ctx.guild.ban(target, reason=reason)
            # If you want to prune recent messages (API supports seconds):
            # await ctx.guild.ban(target, reason=reason, delete_message_seconds=0)
        except discord.Forbidden:
            return await ctx.reply("I don’t have permission to ban that user.")
        except Exception as e:
            return await ctx.reply(f"Error banning user: {e}")

        # Log the action (will increment case counter and store message ref)
        await self._log_case(ctx, target, "Ban", reason, None, dm_ok)





# =============================================
#                     UNBAN
# =============================================
    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True, send_messages=True, embed_links=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason provided"):
        """Unban a member."""
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
        except Exception as e:
            return await ctx.reply(f"Error unbanning member: {e}")

        dm_ok = True
        try:
            await user.send(f"You have been **unbanned** from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        await self._log_case(ctx, user, "Unban", reason, None, dm_ok)





# =============================================
#                   REASON
# =============================================
    @commands.command(name="reason")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def reason_cmd(self, ctx: commands.Context, case_no: int, *, new_reason: str):
        """Update the reason in both the embed and stored warning record (if applicable)."""
        # 1️⃣ Locate the message
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            idx = (cfg.modules or {}).get("case_index", {})
            entry = idx.get(str(case_no))
            if not entry:
                return await send_err(ctx, "Reason Update", f"Case `{case_no}` not found in index.")
            ch_id, msg_id = int(entry["c"]), int(entry["m"])
            stored_uid = int(entry["u"]) if "u" in entry else None

        # 2️⃣ Fetch the log message
        channel = ctx.guild.get_channel(ch_id) or self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await ctx.guild.fetch_channel(ch_id)
            except Exception:
                return await send_err(ctx, "Reason Update", "I can’t access the log channel.")

        try:
            message = await channel.fetch_message(msg_id)
        except Exception:
            return await send_err(ctx, "Reason Update", "Couldn’t fetch the case message.")

        if not message.embeds:
            return await send_err(ctx, "Reason Update", "No embed found in that case message.")

        # 3️⃣ Edit the embed
        emb = message.embeds[0]
        edited = discord.Embed.from_dict(emb.to_dict())
        edited.timestamp = datetime.now(timezone.utc)

        field_index = None
        for i, f in enumerate(edited.fields):
            if f.name.lower().strip() == "reason":
                field_index = i
                break

        if field_index is not None:
            edited.set_field_at(field_index, name="Reason", value=new_reason, inline=False)
        else:
            edited.add_field(name="Reason", value=new_reason, inline=False)

        try:
            await message.edit(embed=edited)
        except Exception as e:
            return await send_err(ctx, "Reason Update", f"Failed to edit embed: `{e}`")

        # 4️⃣ Update stored reason in warning data (if this case belongs to a warn)
        # We'll check if this user has warnings and patch the latest one.
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            warns = (cfg.modules or {}).get("warns", {})

            if stored_uid and str(stored_uid) in warns:
                # Find matching warn case if possible (latest case for same user)
                user_warns = warns[str(stored_uid)]
                if user_warns:
                    user_warns[-1]["reason"] = new_reason  # update last warning
                    cfg.modules["warns"][str(stored_uid)] = user_warns
                    flag_modified(cfg, "modules")
                    session.add(cfg)
                    await session.commit()

        await send_ok(ctx, "Reason Updated", f"Case `{case_no}` reason updated to:\n> {new_reason}")





# =============================================
#              DURATION 
# =============================================
    @commands.command(name="duration")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True, send_messages=True, embed_links=True)
    async def duration_cmd(self, ctx: commands.Context, case_no: int, duration: str):
        """
        Update the embed's Duration field AND the member's actual timeout
        for Mute/Timeout cases.
        """
        ms = parse_duration_ms(duration)
        if ms is None or ms <= 0:
            return await send_err(ctx, "Duration", "Invalid time. Try `30m`, `2h`, `1d`, `1h30m`.")

        # Discord timeout hard limit ~28 days
        max_ms = 28 * 24 * 60 * 60 * 1000
        if ms > max_ms:
            return await send_err(ctx, "Duration", "Duration too long. Max is **28d**.")

        # 1) Find the logged message (and user id, if stored)
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            idx = (cfg.modules or {}).get("case_index", {})
            entry = idx.get(str(case_no))
            if not entry:
                return await send_err(ctx, "Duration", "I can't find that case in my index.")
            ch_id, msg_id = int(entry["c"]), int(entry["m"])
            stored_uid = int(entry["u"]) if "u" in entry else None

        # 2) Fetch the message + embed
        channel = ctx.guild.get_channel(ch_id) or self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await ctx.guild.fetch_channel(ch_id)  # type: ignore
            except Exception:
                return await send_err(ctx, "Duration", "I can't access the log channel for that case.")

        try:
            message: discord.Message = await channel.fetch_message(msg_id)  # type: ignore
        except Exception:
            return await send_err(ctx, "Duration", "I couldn't fetch the original case message.")

        if not message.embeds:
            return await send_err(ctx, "Duration", "That case message has no embed to edit.")
        emb = message.embeds[0]

        # 3) Determine if this is a Mute/Timeout case from the author line
        # Expected: "Case N | <Action> | <username>"
        action_name = ""
        try:
            author_title = emb.author.name or ""
            parts = [p.strip().lower() for p in author_title.split("|")]
            if len(parts) >= 2:
                action_name = parts[1]  # e.g., 'mute' or 'timeout'
        except Exception:
            pass

        is_timeout_case = action_name in {"mute", "timeout"}

        # 4) Resolve user id to apply timeout (from index or fallback parse from "User" field)
        uid = stored_uid
        if uid is None:
            for f in emb.fields:
                if f.name.strip().lower() == "user":
                    # Value is like: "<@1234> (`1234`)" or "<@1234>"
                    m = re.search(r"\(`(\d+)`\)", f.value)
                    if not m:
                        m = re.search(r"<@!?(?P<id>\d+)>", f.value)
                    if m:
                        uid = int(m.group(1) if "id" not in m.groupdict() else m.group("id"))
                    break

        member_for_timeout: discord.Member | None = None
        if is_timeout_case and uid:
            member_for_timeout = ctx.guild.get_member(uid)
            if member_for_timeout is None:
                try:
                    member_for_timeout = await ctx.guild.fetch_member(uid)
                except Exception:
                    member_for_timeout = None

        # 5) Update the embed Duration field
        edited = discord.Embed.from_dict(emb.to_dict())
        edited.timestamp = datetime.now(timezone.utc)

        idx_field = None
        for i, f in enumerate(edited.fields):
            if f.name.strip().lower() == "duration":
                idx_field = i
                break

        human = humanize_ms(ms)
        if idx_field is not None:
            edited.set_field_at(idx_field, name="Duration", value=human, inline=True)
        else:
            edited.add_field(name="Duration", value=human, inline=True)

        # 6) If this was a Mute/Timeout case and we have the member, apply the new timeout
        # (This overwrites any existing timeout with "now + new_duration")
        if is_timeout_case and member_for_timeout:
            try:
                until = datetime.now(timezone.utc) + timedelta(milliseconds=ms)
                await member_for_timeout.timeout(until, reason=f"Duration updated by {ctx.author} to {duration}")
            except discord.Forbidden:
                return await send_err(ctx, "Duration", "I don’t have permission to change that member’s timeout.")
            except Exception as e:
                return await send_err(ctx, "Duration", f"Failed to update the actual timeout: `{e}`")

        # 7) Save the embed update
        try:
            await message.edit(embed=edited)
        except Exception as e:
            return await send_err(ctx, "Duration", f"Failed to edit the log message: `{e}`")

        if is_timeout_case and member_for_timeout:
            await send_ok(ctx, "Duration Updated", f"Case `{case_no}` set to **{duration}** and timeout changed.")
        elif is_timeout_case and not member_for_timeout:
            await send_warn(ctx, "Duration Updated", f"Embed updated for case `{case_no}`, but I couldn't find the member to update their timeout.")
        else:
            await send_info(ctx, "Duration Updated", f"Embed updated for case `{case_no}` (not a mute/timeout case).")






# =============================================
#                    CLEAN
# =============================================
    @commands.command(name="clean")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def clean(self, ctx: commands.Context, limit: int = 50):
        pref = PREFIX or ";"

        def check(msg: discord.Message):
            content = (msg.content or "").strip()
            return (msg.author.id == ctx.bot.user.id) or content.startswith(pref)

        try:
            deleted = await ctx.channel.purge(limit=limit, check=check, bulk=True)
        except discord.Forbidden:
            return await send_err(ctx, "Clean Failed", "I don’t have permission to delete messages here.")
        except Exception as e:
            return await send_err(ctx, "Clean Failed", f"`{e}`")

        note = await send_ok(ctx, "Clean", f"Deleted **{len(deleted)}** bot/command messages.")
        await asyncio.sleep(4)
        with contextlib.suppress(Exception):
            await note.delete()





# =============================================
#                    PURGE
# =================================
    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, limit: int, *, filters: str = ""):
        target_user_id: Optional[int] = None
        contains_text: Optional[str] = None

        m = re.search(r"<@!?(?P<id>\d+)>", filters)
        if m:
            target_user_id = int(m.group("id"))
        m2 = re.search(r"contains:(?P<text>.+)", filters, re.IGNORECASE)
        if m2:
            contains_text = m2.group("text").strip().lower()

        def check(msg: discord.Message):
            if target_user_id and msg.author.id != target_user_id:
                return False
            if contains_text and contains_text not in (msg.content or "").lower():
                return False
            return True

        try:
            deleted = await ctx.channel.purge(limit=limit, check=check, bulk=True)
        except discord.Forbidden:
            return await send_err(ctx, "Purge Failed", "I don’t have permission to delete messages here.")
        except Exception as e:
            return await send_err(ctx, "Purge Failed", f"`{e}`")

        note = await send_ok(ctx, "Purge", f"Purged **{len(deleted)}** messages.")
        await asyncio.sleep(4)
        with contextlib.suppress(Exception):
            await note.delete()





# =============================================                 
#                   SLOWMODE
# =============================================
    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, delay: str = None):
        if delay is None:
            current = ctx.channel.slowmode_delay
            return await send_info(ctx, "Slowmode", f"Current slowmode: **{current}s**")

        if delay.lower() == "off":
            seconds = 0
        else:
            try:
                seconds = int(delay)
            except ValueError:
                return await send_err(ctx, "Slowmode", "Please enter a number (seconds) or `off`.")

        try:
            await ctx.channel.edit(slowmode_delay=seconds, reason=f"Set by {ctx.author}")
        except discord.Forbidden:
            return await send_err(ctx, "Slowmode", "I don't have permission to manage this channel.")
        except Exception as e:
            return await send_err(ctx, "Slowmode", f"`{e}`")

        if seconds == 0:
            await send_ok(ctx, "Slowmode", f"Disabled in {ctx.channel.mention}.")
        else:
            await send_ok(ctx, "Slowmode", f"Set to **{seconds} seconds** in {ctx.channel.mention}.")




# =============================================                 
#                   LOCK
# =============================================
    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *, reason: str = "No reason provided"):
        channel = channel or ctx.channel #type: ignore
        if not isinstance(channel, discord.TextChannel):
            return await send_err(ctx, "Lock", "Please target a text channel.")
        overwrites = channel.overwrites_for(ctx.guild.default_role) #type: ignore
        if overwrites.send_messages is False:
            return await send_info(ctx, "Lock", f"{channel.mention} is already locked.")
        overwrites.send_messages = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=reason) #type: ignore
        except discord.Forbidden:
            return await send_err(ctx, "Lock", "I don’t have permission to edit channel permissions here.")
        await send_ok(ctx, "Lock", f"🔒 Locked {channel.mention}\nReason: {reason}")





# =============================================                 
#                   UNLOCK
# =============================================
    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel #type: ignore
        if not isinstance(channel, discord.TextChannel):
            return await send_err(ctx, "Unlock", "Please target a text channel.")
        overwrites = channel.overwrites_for(ctx.guild.default_role) #type: ignore
        if overwrites.send_messages is True:
            return await send_info(ctx, "Unlock", f"{channel.mention} is already unlocked.")
        overwrites.send_messages = True
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=f"Unlock by {ctx.author}") #type: ignore
        except discord.Forbidden:
            return await send_err(ctx, "Unlock", "I don’t have permission to edit channel permissions here.")
        await send_ok(ctx, "Unlock", f"🔓 Unlocked {channel.mention}.")





# =============================================
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
