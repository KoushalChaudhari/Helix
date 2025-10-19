# cogs/mod.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.engine import AsyncSessionLocal
from db.models import GuildConfig


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


def _set_case_index_entry(cfg: GuildConfig, case_no: int, channel_id: int, message_id: int) -> None:
    """Store where we logged a case so we can edit it later."""
    mods = cfg.modules or {}
    idx = mods.get("case_index") or {}
    idx[str(case_no)] = {"c": str(channel_id), "m": str(message_id)}
    mods["case_index"] = idx
    cfg.modules = mods
    flag_modified(cfg, "modules")  # ensure JSON mutation is persisted


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
    """Return a compact '1d2h30m' style string."""
    parts: list[str] = []
    for unit, size in (("w", 604_800_000), ("d", 86_400_000), ("h", 3_600_000), ("m", 60_000), ("s", 1_000)):
        if ms >= size:
            n, ms = divmod(ms, size)
            parts.append(f"{n}{unit}")
    return "".join(parts) or "0s"


# ──────────────────────────────────────────────────────────────────────────────
# Moderation Cog
# ──────────────────────────────────────────────────────────────────────────────
class Moderation(commands.Cog):
    """Moderation: warn + mod-log config + edit logged cases (no case DB rows)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_case(self, ctx: commands.Context, target, action: str, reason: str, duration: str | None, dm_ok: bool):
        """Create the log embed, send it (mod-log or fallback), and index the case."""
        # Reserve case number + read modlog config
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            case_no = next_case_number(cfg)
            modlog_id = get_modlog_channel_id(cfg)
            session.add(cfg)
            await session.commit()

        # Build embed
        embed = discord.Embed(color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        target_name = getattr(target, "name", str(target))
        icon = getattr(target, "display_avatar", None)
        embed.set_author(
            name=f"Case {case_no} | {action} | {target_name}",
            icon_url=icon.url if icon else discord.Embed.Empty,
        )
        user_mention = getattr(target, "mention", None) or f"`{getattr(target, 'id', '')}`"
        embed.add_field(name="User", value=user_mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=(reason[:1024] or "No reason provided"), inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.set_footer(text=f"ID: {getattr(target, 'id', '')}")

        # Resolve target channel robustly: cache → global → API fetch
        target_channel: Optional[discord.abc.MessageableChannel] = None
        if modlog_id:
            target_channel = ctx.guild.get_channel(modlog_id) or self.bot.get_channel(modlog_id)
            if target_channel is None:
                try:
                    target_channel = await ctx.guild.fetch_channel(modlog_id)  # type: ignore
                except Exception as e:
                    print(f"[modlog] fetch_channel failed for {modlog_id}: {e}")

        # Send embed (log) and remember where it went
        sent_where = "mod-log channel"
        logged_message: Optional[discord.Message] = None
        try:
            if target_channel:
                logged_message = await target_channel.send(embed=embed)  # type: ignore
            else:
                sent_where = "this channel (mod-log not set or not accessible)"
                logged_message = await ctx.channel.send(embed=embed)
        except Exception as e:
            sent_where = f"failed to log ({e})"

        if logged_message is not None:
            # Store location of the case so we can edit later
            async with AsyncSessionLocal() as session:
                cfg = await get_guild_cfg(session, str(ctx.guild.id))
                _set_case_index_entry(cfg, case_no, logged_message.channel.id, logged_message.id)
                session.add(cfg)
                await session.commit()

        note = "and DM sent." if dm_ok else "and DM **could not** be delivered (user’s DMs closed)."
        # Reply to the command invoker
        await ctx.reply(f"{action} logged for {user_mention} — Case `{case_no}` logged to {sent_where} {note}")

    # ── Configure / View mod-log channel ──────────────────────────────────────
    @commands.command(name="modlog")
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True)
    async def modlog(self, ctx: commands.Context, channel: discord.TextChannel | None = None):
        """
        Set or view the moderation log channel.
        Usage:
          ;modlog #channel   → set
          ;modlog            → show current
        """
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))

            if channel is None:
                cid = get_modlog_channel_id(cfg)
                if not cid:
                    return await ctx.reply("Current mod-log channel: not set")

                ch = ctx.guild.get_channel(cid) or self.bot.get_channel(cid)
                if ch is None:
                    try:
                        ch = await ctx.guild.fetch_channel(cid)  # type: ignore
                    except Exception:
                        ch = None
                return await ctx.reply(f"Current mod-log channel: {ch.mention if ch else f'`{cid}` (not accessible)'}")

            set_modlog_channel_id(cfg, channel.id)
            session.add(cfg)
            await session.commit()

        await ctx.reply(f"Mod-log channel set to {channel.mention}")

    # ── Warn (DM user + log embed; per-guild case numbers) ────────────────────
    @commands.command()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """
        Warn a member:
          • DMs the user (reason + server name)
          • Logs an embed to the configured mod-log channel
          • Case number increments per guild (1, 2, 3, …)
        """
        if member.bot:
            return await ctx.reply("I won’t warn bots.")
        if member == ctx.author:
            return await ctx.reply("You can’t warn yourself.")

        # Try DM (non-fatal if blocked)
        dm_ok = True
        try:
            await member.send(
                f"You have been **warned** in **{ctx.guild.name}**.\n"
                f"**Reason:** {reason}"
            )
        except Exception:
            dm_ok = False

        # Get next case number + mod-log channel id
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            case_no = next_case_number(cfg)
            modlog_id = get_modlog_channel_id(cfg)
            session.add(cfg)
            await session.commit()

        # Build the log embed
        embed = discord.Embed(
            color=discord.Color.yellow(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f"Case {case_no} | Warn | {member.name}",
            icon_url=member.display_avatar.url if member.display_avatar else discord.Embed.Empty,
        )
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=(reason[:1024] or "No reason provided"), inline=False)
        embed.set_footer(text=f"ID: {member.id}")

        # Resolve target channel robustly: cache → global → API fetch
        target_channel: Optional[discord.abc.MessageableChannel] = None
        if modlog_id:
            target_channel = ctx.guild.get_channel(modlog_id) or self.bot.get_channel(modlog_id)
            if target_channel is None:
                try:
                    target_channel = await ctx.guild.fetch_channel(modlog_id)  # type: ignore
                except Exception as e:
                    print(f"[modlog] fetch_channel failed for {modlog_id}: {e}")

        # Send embed (log) and remember where it went
        sent_where = "mod-log channel"
        logged_message: Optional[discord.Message] = None
        try:
            if target_channel:
                logged_message = await target_channel.send(embed=embed)  # type: ignore
            else:
                sent_where = "this channel (mod-log not set or not accessible)"
                logged_message = await ctx.channel.send(embed=embed)
        except Exception as e:
            sent_where = f"failed to log ({e})"

        if logged_message is not None:
            # Store location of the case so we can edit later
            async with AsyncSessionLocal() as session:
                cfg = await get_guild_cfg(session, str(ctx.guild.id))
                _set_case_index_entry(cfg, case_no, logged_message.channel.id, logged_message.id)
                session.add(cfg)
                await session.commit()

        note = "and DM sent." if dm_ok else "and DM **could not** be delivered (user’s DMs closed)."
        await ctx.reply(f"⚠️ Warned **{member}** — Case `{case_no}` logged to {sent_where} {note}")

    # Mute
    @commands.command()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True, send_messages=True, embed_links=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = "No reason provided"):
        """Timeout (mute) a member for a given duration."""
        if member.bot:
            return await ctx.reply("You can’t mute bots.")
        if member == ctx.author:
            return await ctx.reply("You can’t mute yourself.")

        ms = parse_duration_ms(duration)
        if ms is None:
            return await ctx.reply("Invalid duration. Try `10m`, `2h`, `1d`.")

        # Convert ms → seconds (Discord timeouts use seconds)
        seconds = ms / 1000
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

        try:
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            return await ctx.reply("I don’t have permission to mute that member.")
        except Exception as e:
            return await ctx.reply(f"Error muting member: {e}")

        # DM user
        dm_ok = True
        try:
            await member.send(f"You have been **muted** in **{ctx.guild.name}** for **{duration}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        # Log the mute
        await self._log_case(ctx, member, "Mute", reason, duration, dm_ok)

    # Unmute
    @commands.command()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True, send_messages=True, embed_links=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Remove timeout (mute) from a member."""
        try:
            await member.timeout(None, reason=reason)
        except Exception as e:
            return await ctx.reply(f"Error unmuting member: {e}")

        dm_ok = True
        try:
            await member.send(f"You have been **unmuted** in **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        await self._log_case(ctx, member, "Unmute", reason, None, dm_ok)

    # Kick
    @commands.command()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True, send_messages=True, embed_links=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Kick a member."""
        if member == ctx.author:
            return await ctx.reply("You can’t kick yourself.")
        try:
            await member.kick(reason=reason)
        except Exception as e:
            return await ctx.reply(f"Error kicking member: {e}")

        dm_ok = True
        try:
            await member.send(f"You have been **kicked** from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        await self._log_case(ctx, member, "Kick", reason, None, dm_ok)

    # Ban
    @commands.command()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True, send_messages=True, embed_links=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """Ban a member."""
        if member == ctx.author:
            return await ctx.reply("You can’t ban yourself.")
        try:
            await member.ban(reason=reason, delete_message_days=0)
        except Exception as e:
            return await ctx.reply(f"Error banning member: {e}")

        dm_ok = True
        try:
            await member.send(f"You have been **banned** from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False

        await self._log_case(ctx, member, "Ban", reason, None, dm_ok)

    # Unban
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

    # ── Edit Reason on a logged case ──────────────────────────────────────────
    @commands.command(name="reason")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def reason_cmd(self, ctx: commands.Context, case_no: int, *, new_reason: str):
        """Update the Reason field of a logged case embed."""
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            idx = _get_case_index(cfg)
            entry = idx.get(str(case_no))
            if not entry:
                return await ctx.reply("I can't find that case in my index.")
            ch_id, msg_id = int(entry["c"]), int(entry["m"])

        # Fetch channel/message
        channel = ctx.guild.get_channel(ch_id) or self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await ctx.guild.fetch_channel(ch_id)  # type: ignore
            except Exception:
                return await ctx.reply("I can't access the log channel for that case.")

        try:
            message: discord.Message = await channel.fetch_message(msg_id)  # type: ignore
        except Exception:
            return await ctx.reply("I couldn't fetch the original case message.")

        if not message.embeds:
            return await ctx.reply("That case message has no embed to edit.")

        # Rebuild & edit the embed
        base = discord.Embed.from_dict(message.embeds[0].to_dict())
        idx_field = _find_field(base, "Reason")
        value = (new_reason[:1024] or "No reason provided")

        if idx_field is None:
            base.add_field(name="Reason", value=value, inline=False)
        else:
            fields = list(base.fields)
            # Replace field at idx_field
            fields[idx_field] = discord.EmbedField(name="Reason", value=value, inline=False)  # type: ignore[attr-defined]
            base.clear_fields()
            for f in fields:
                base.add_field(name=f.name, value=f.value, inline=f.inline)

        base.timestamp = datetime.now(timezone.utc)

        try:
            await message.edit(embed=base)
        except Exception as e:
            return await ctx.reply(f"Failed to edit the log message: {e}")

        await ctx.reply(f"📝 Updated reason for case `{case_no}`.")

    # ── Add/Update Duration on a logged case ──────────────────────────────────
    @commands.command(name="duration")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def duration_cmd(self, ctx: commands.Context, case_no: int, duration: str):
        """Add or change a Duration field on a logged case (e.g., 30m, 2h, 1d)."""
        ms = parse_duration_ms(duration)
        if ms is None:
            return await ctx.reply("Invalid time. Try `30m`, `2h`, `1d`, or `1h30m`.")

        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, str(ctx.guild.id))
            idx = _get_case_index(cfg)
            entry = idx.get(str(case_no))
            if not entry:
                return await ctx.reply("I can't find that case in my index.")
            ch_id, msg_id = int(entry["c"]), int(entry["m"])

        # Fetch channel/message
        channel = ctx.guild.get_channel(ch_id) or self.bot.get_channel(ch_id)
        if channel is None:
            try:
                channel = await ctx.guild.fetch_channel(ch_id)  # type: ignore
            except Exception:
                return await ctx.reply("I can't access the log channel for that case.")

        try:
            message: discord.Message = await channel.fetch_message(msg_id)  # type: ignore
        except Exception:
            return await ctx.reply("I couldn't fetch the original case message.")

        if not message.embeds:
            return await ctx.reply("That case message has no embed to edit.")

        # Rebuild & edit the embed
        base = discord.Embed.from_dict(message.embeds[0].to_dict())
        idx_field = _find_field(base, "Duration")
        value = humanize_ms(ms)

        if idx_field is None:
            base.add_field(name="Duration", value=value, inline=True)
        else:
            fields = list(base.fields)
            fields[idx_field] = discord.EmbedField(name="Duration", value=value, inline=True)  # type: ignore[attr-defined]
            base.clear_fields()
            for f in fields:
                base.add_field(name=f.name, value=f.value, inline=f.inline)

        base.timestamp = datetime.now(timezone.utc)

        try:
            await message.edit(embed=base)
        except Exception as e:
            return await ctx.reply(f"Failed to edit the log message: {e}")

        await ctx.reply(f"⏱ Updated duration for case `{case_no}` to **{duration}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
