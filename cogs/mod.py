# cogs/mod.py
from __future__ import annotations
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.engine import AsyncSessionLocal
from db.models import GuildConfig

# --------- Theme / helpers ----------
HELIX_PRIMARY = discord.Color.from_rgb(110, 82, 255)
HELIX_SUCCESS = discord.Color.from_rgb(60, 180, 150)
HELIX_WARN = discord.Color.gold()
HELIX_ERROR = discord.Color.from_rgb(255, 85, 160)
FOOTER_TEXT = "⚙️ Helix Moderation System"

def mkembed(title: str, desc: str = "", color: discord.Color = HELIX_PRIMARY) -> discord.Embed:
    emb = discord.Embed(title=title, description=desc or "", color=color, timestamp=datetime.now(timezone.utc))
    return emb

async def send_simple(ctx: commands.Context, title: str, desc: str = "", color: discord.Color = HELIX_PRIMARY):
    e = mkembed(title, desc, color)
    try:
        e.set_footer(text=FOOTER_TEXT, icon_url=(ctx.bot.user.display_avatar.url if getattr(ctx.bot.user, "display_avatar", None) else None))
    except Exception:
        pass
    return await ctx.send(embed=e)

# --------- DB helpers ----------
async def get_guild_cfg(session, guild_id: int) -> GuildConfig:
    gid = str(guild_id)
    res = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == gid))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = GuildConfig(id=uuid.uuid4().hex, guild_id=gid, prefix=";", modules={})
        session.add(cfg)
        await session.commit()
    if cfg.modules is None:
        cfg.modules = {}
    return cfg

def _next_case_seq(cfg: GuildConfig) -> int:
    mods = cfg.modules or {}
    seq = int(mods.get("case_seq", 0)) + 1
    mods["case_seq"] = str(seq)
    cfg.modules = mods
    flag_modified(cfg, "modules")
    return seq

def _index_case(cfg: GuildConfig, case_no: int, channel_id: int, message_id: int, user_id: Optional[int] = None):
    mods = cfg.modules or {}
    idx = mods.get("case_index") or {}
    if not isinstance(idx, dict):
        idx = {}
    idx[str(case_no)] = {"c": str(channel_id), "m": str(message_id)}
    if user_id is not None:
        idx[str(case_no)]["u"] = str(user_id)
    mods["case_index"] = idx
    cfg.modules = mods
    flag_modified(cfg, "modules")

def _get_modlog_id(mods: Dict[str, Any]) -> Optional[int]:
    v = mods.get("modlog_channel_id")
    if not v:
        return None
    try:
        return int(v)
    except Exception:
        return None

# --------- utility parsers -----------
_UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
def parse_duration_ms(s: str) -> Optional[int]:
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

def _resolve_member_by_query(guild: discord.Guild, query: str) -> Optional[discord.Member]:
    if not guild:
        return None
    # mention/id
    m = re.search(r"(\d{15,25})", query)
    if m:
        try:
            uid = int(m.group(1))
            mem = guild.get_member(uid)
            if mem:
                return mem
        except Exception:
            pass
    # username#discrim
    if "#" in query:
        try:
            name, discrim = query.rsplit("#", 1)
            mem = discord.utils.get(guild.members, name=name, discriminator=discrim)
            if mem:
                return mem
        except Exception:
            pass
    # exact display/name
    mem = discord.utils.find(lambda mm: (mm.name and mm.name.lower() == query.lower()) or (mm.display_name and mm.display_name.lower() == query.lower()), guild.members)
    if mem:
        return mem
    # partial
    mem = discord.utils.find(lambda mm: query.lower() in (mm.name or "").lower() or (mm.display_name and query.lower() in mm.display_name.lower()), guild.members)
    return mem

# --------- Moderation Cog ----------
class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # central case logger (posts to mod-log channel if set)
    async def _log_case(self, ctx: commands.Context, target: discord.abc.User, action: str, reason: str, duration: Optional[str], dm_ok: bool) -> int:
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            case_no = _next_case_seq(cfg)
            modlog_id = _get_modlog_id(cfg.modules or {})
            session.add(cfg)
            await session.commit()

        color = HELIX_PRIMARY
        embed = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
        try:
            embed.set_author(name=f"Case {case_no} • {action} • {getattr(target,'name', str(target))}", icon_url=(getattr(target, "display_avatar", None).url if getattr(target, "display_avatar", None) else None))
        except Exception:
            embed.set_author(name=f"Case {case_no} • {action} • {getattr(target,'name', str(target))}")
        embed.add_field(name="User", value=f"{getattr(target,'mention', str(target))} (`{getattr(target,'id','')}`)", inline=True)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=(reason or "No reason provided")[:1024], inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)

        send_channel = None
        if modlog_id:
            try:
                send_channel = ctx.guild.get_channel(modlog_id) or self.bot.get_channel(modlog_id)
                if send_channel is None:
                    send_channel = await ctx.guild.fetch_channel(modlog_id)  # type: ignore
            except Exception:
                send_channel = None
        send_channel = send_channel or ctx.channel
        msg = await send_channel.send(embed=embed)

        # index case for later edits
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            _index_case(cfg, case_no, msg.channel.id, msg.id, getattr(target, "id", None))
            session.add(cfg)
            await session.commit()

        summary = mkembed(f"{getattr(target,'name', str(target))} — {action}", f"Reason: {reason}" + (f"\nDuration: {duration}" if duration else "") + ("\nDM sent." if dm_ok else "\nDM failed."), HELIX_PRIMARY)
        summary.set_footer(text=f"Case {case_no} • Moderator: {ctx.author}", icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
        await ctx.send(embed=summary)
        return case_no

    # ---------- modlog command ----------
    @commands.command(name="modlog")
    @commands.has_permissions(manage_guild=True)
    async def modlog(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            cur = (cfg.modules or {}).get("modlog_channel_id")
            if channel is None:
                if not cur:
                    return await send_simple(ctx, "Mod-log", "No mod-log channel set. Use `;modlog #channel`.", HELIX_WARN)
                try:
                    ch = ctx.guild.get_channel(int(cur)) or self.bot.get_channel(int(cur))
                except Exception:
                    ch = None
                if ch:
                    return await send_simple(ctx, "Mod-log", f"Current mod-log channel: {ch.mention}", HELIX_PRIMARY)
                return await send_simple(ctx, "Mod-log", f"Mod-log set to ID `{cur}` but I can't access it.", HELIX_WARN)
            mods = cfg.modules or {}
            mods["modlog_channel_id"] = str(channel.id)
            cfg.modules = mods
            flag_modified(cfg, "modules")
            session.add(cfg)
            await session.commit()
        await send_simple(ctx, "Mod-log Saved", f"Mod-log channel set to {channel.mention}", HELIX_SUCCESS)

    # ---------- warn / warns / clearwarns ----------
    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if member.bot:
            return await send_simple(ctx, "Invalid Target", "You cannot warn bots.", HELIX_WARN)
        dm_ok = True
        try:
            await member.send(f"You were warned in **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            mods = cfg.modules or {}
            warns = mods.get("warns", {})
            user_warns = warns.get(str(member.id), [])
            user_warns.append({"reason": reason, "moderator": str(ctx.author.id), "timestamp": datetime.now(timezone.utc).isoformat()})
            warns[str(member.id)] = user_warns
            mods["warns"] = warns
            cfg.modules = mods
            flag_modified(cfg, "modules")
            session.add(cfg)
            await session.commit()
        await self._log_case(ctx, member, "Warn", reason, None, dm_ok)

    @commands.command(name="warns", aliases=["warnings"])
    async def warns(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            warns_map = (cfg.modules or {}).get("warns", {})
            user_warns = warns_map.get(str(member.id), [])
        if not user_warns:
            return await send_simple(ctx, "Warnings", f"{member.mention} has no warnings.", HELIX_PRIMARY)
        embed = mkembed(f"Warnings — {member}", color=HELIX_WARN)
        for i, w in enumerate(user_warns, 1):
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
            embed.add_field(name=f"{i}. {w['reason']}", value=f"Moderator: <@{w['moderator']}> • {ts}", inline=False)
        embed.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns", aliases=["clearwarnings"])
    @commands.has_permissions(manage_messages=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            mods = cfg.modules or {}
            warns_map = mods.get("warns", {})
            if str(member.id) in warns_map:
                warns_map.pop(str(member.id))
                mods["warns"] = warns_map
                cfg.modules = mods
                flag_modified(cfg, "modules")
                session.add(cfg)
                await session.commit()
                return await send_simple(ctx, "Clear Warnings", f"Cleared all warnings for {member.mention}.", HELIX_SUCCESS)
        await send_simple(ctx, "Clear Warnings", f"{member.mention} has no warnings.", HELIX_WARN)

    # ---------- muterole config ----------
    @commands.command(name="muterole")
    @commands.has_permissions(manage_roles=True)
    async def muterole(self, ctx: commands.Context, role: Optional[discord.Role] = None):
        """
        ;muterole @Muted  → set muted role
        ;muterole         → show current
        ;muterole none    → clear
        """
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            mods = cfg.modules or {}
            cur = mods.get("muted_role_id")
            if role is None:
                if ctx.message.content.strip().lower().endswith("none"):
                    mods.pop("muted_role_id", None)
                    cfg.modules = mods
                    flag_modified(cfg, "modules")
                    session.add(cfg)
                    await session.commit()
                    emb = mkembed("🔇 Muted Role Cleared", "Muted role removed.", HELIX_WARN)
                    emb.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
                    return await ctx.send(embed=emb)
                if cur:
                    try:
                        r = ctx.guild.get_role(int(cur))
                    except Exception:
                        r = None
                    if r:
                        return await ctx.send(embed=mkembed("🔇 Muted Role", f"Currently: {r.mention}", HELIX_PRIMARY))
                    return await ctx.send(embed=mkembed("🔇 Muted Role", f"Currently set to ID `{cur}` but role not found.", HELIX_WARN))
                return await ctx.send(embed=mkembed("🔇 Muted Role", "No muted role set. Use `;muterole @Muted`.", HELIX_WARN))
            mods["muted_role_id"] = str(role.id)
            cfg.modules = mods
            flag_modified(cfg, "modules")
            session.add(cfg)
            await session.commit()
        emb = mkembed("🔇 Muted Role Saved", f"Muted role set to {role.mention}.", HELIX_SUCCESS)
        emb.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
        await ctx.send(embed=emb)

    # ---------- mute / unmute ----------
    @commands.command(name="mute")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            role_id = (cfg.modules or {}).get("muted_role_id")
        if not role_id:
            return await send_simple(ctx, "No Muted Role", "No muted role set. Use `;muterole @Muted`.", HELIX_WARN)
        role = ctx.guild.get_role(int(role_id))
        if not role:
            return await send_simple(ctx, "Muted Role Missing", "Configured muted role doesn't exist. Re-set with `;muterole @Muted`.", HELIX_WARN)
        if role in member.roles:
            return await send_simple(ctx, "Already Muted", f"{member.mention} already has {role.mention}.", HELIX_WARN)
        me = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
        if me and role >= me.top_role:
            return await send_simple(ctx, "Permission Error", "I cannot manage that role because it is equal or higher than my top role.", HELIX_ERROR)
        try:
            await member.add_roles(role, reason=f"Muted by {ctx.author}: {reason}")
        except discord.Forbidden:
            return await send_simple(ctx, "Forbidden", "I don't have permission to add that role.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Mute Failed", f"Failed to mute: `{e}`", HELIX_ERROR)
        dm_ok = True
        try:
            await member.send(f"You have been muted in **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False
        await self._log_case(ctx, member, "Mute", reason, None, dm_ok)

    @commands.command(name="unmute")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            role_id = (cfg.modules or {}).get("muted_role_id")
        if not role_id:
            return await send_simple(ctx, "No Muted Role", "No muted role configured. Use `;muterole @Muted`.", HELIX_WARN)
        role = ctx.guild.get_role(int(role_id))
        if not role:
            return await send_simple(ctx, "Muted Role Missing", "Configured muted role doesn't exist. Re-set it with `;muterole @Muted`.", HELIX_WARN)
        if role not in member.roles:
            return await send_simple(ctx, "Not Muted", f"{member.mention} does not have {role.mention}.", HELIX_WARN)
        try:
            await member.remove_roles(role, reason=f"Unmuted by {ctx.author}")
        except discord.Forbidden:
            return await send_simple(ctx, "Forbidden", "I don't have permission to remove that role.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Unmute Failed", f"Failed to unmute: `{e}`", HELIX_ERROR)
        dm_ok = True
        try:
            await member.send(f"You have been unmuted in **{ctx.guild.name}**.")
        except Exception:
            dm_ok = False
        await self._log_case(ctx, member, "Unmute", "Unmuted", None, dm_ok)

    # ---------- kick / ban / unban ----------
    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if member == ctx.author:
            return await send_simple(ctx, "Invalid Target", "You cannot kick yourself.", HELIX_WARN)
        if member.bot:
            return await send_simple(ctx, "Invalid Target", "You cannot kick bots.", HELIX_WARN)
        dm_ok = True
        try:
            await member.send(f"You have been kicked from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await send_simple(ctx, "Forbidden", "I don't have permission to kick that member.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Kick Failed", f"Failed to kick: `{e}`", HELIX_ERROR)
        await self._log_case(ctx, member, "Kick", reason, None, dm_ok)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, target: discord.User, *, reason: str = "No reason provided"):
        if isinstance(target, discord.Member) and target == ctx.author:
            return await send_simple(ctx, "Invalid Target", "You cannot ban yourself.", HELIX_WARN)
        if target.bot:
            return await send_simple(ctx, "Invalid Target", "You cannot ban bots.", HELIX_WARN)
        dm_ok = True
        try:
            await target.send(f"You have been banned from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False
        try:
            await ctx.guild.ban(target, reason=reason)
        except discord.Forbidden:
            return await send_simple(ctx, "Forbidden", "I don't have permission to ban that user.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Ban Failed", f"Failed to ban: `{e}`", HELIX_ERROR)
        await self._log_case(ctx, target, "Ban", reason, None, dm_ok)

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "No reason provided"):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
        except Exception as e:
            return await send_simple(ctx, "Unban Failed", f"Failed to unban: `{e}`", HELIX_ERROR)
        dm_ok = True
        try:
            await user.send(f"You have been unbanned from **{ctx.guild.name}**.\nReason: {reason}")
        except Exception:
            dm_ok = False
        await self._log_case(ctx, user, "Unban", reason, None, dm_ok)

    # ---------- reason / duration editing ----------
    async def _find_case_message(self, ctx: commands.Context, case_no: int) -> Optional[discord.Message]:
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            idx = (cfg.modules or {}).get("case_index", {})
            entry = idx.get(str(case_no)) if isinstance(idx, dict) else None
        if not entry:
            return None
        try:
            ch_id = int(entry["c"])
            msg_id = int(entry["m"])
            ch = ctx.guild.get_channel(ch_id) or self.bot.get_channel(ch_id)
            if not ch:
                ch = await ctx.guild.fetch_channel(ch_id)
            msg = await ch.fetch_message(msg_id)
            return msg
        except Exception:
            return None

    @commands.command(name="reason")
    @commands.has_permissions(manage_messages=True)
    async def reason_cmd(self, ctx: commands.Context, case_no: int, *, new_reason: str):
        msg = await self._find_case_message(ctx, case_no)
        if not msg:
            return await send_simple(ctx, "Case Not Found", f"Could not find case #{case_no}.", HELIX_WARN)
        try:
            if not msg.embeds:
                return await send_simple(ctx, "Not Editable", "Case message does not contain an embed I can edit.", HELIX_WARN)
            emb = discord.Embed.from_dict(msg.embeds[0].to_dict())
            for f in emb.fields:
                if f.name.lower() == "reason":
                    f.value = new_reason[:1024]
                    break
            else:
                emb.add_field(name="Reason", value=new_reason[:1024], inline=False)
            await msg.edit(embed=emb)
            await send_simple(ctx, "Reason Updated", f"Updated reason for case #{case_no}.", HELIX_SUCCESS)
        except Exception as e:
            return await send_simple(ctx, "Edit Failed", f"Failed to edit case message: `{e}`", HELIX_ERROR)

    @commands.command(name="duration")
    @commands.has_permissions(manage_messages=True)
    async def duration_cmd(self, ctx: commands.Context, case_no: int, duration: str):
        ms = parse_duration_ms(duration)
        if ms is None:
            return await send_simple(ctx, "Invalid Duration", "Please use numbers + units like `10m`, `2h`, `1d`.", HELIX_WARN)
        human = humanize_ms(ms)
        msg = await self._find_case_message(ctx, case_no)
        if not msg:
            return await send_simple(ctx, "Case Not Found", f"Could not find case #{case_no}.", HELIX_WARN)
        try:
            if not msg.embeds:
                return await send_simple(ctx, "Not Editable", "Case message does not contain an embed I can edit.", HELIX_WARN)
            emb = discord.Embed.from_dict(msg.embeds[0].to_dict())
            for f in emb.fields:
                if f.name.lower() == "duration":
                    f.value = human
                    break
            else:
                emb.add_field(name="Duration", value=human, inline=True)
            await msg.edit(embed=emb)
            await send_simple(ctx, "Duration Updated", f"Set duration for case #{case_no} to {human}.", HELIX_SUCCESS)
        except Exception as e:
            return await send_simple(ctx, "Edit Failed", f"Failed to edit case message: `{e}`", HELIX_ERROR)

    # ---------- clean / purge ----------
    @commands.command(name="clean")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def clean(self, ctx: commands.Context, limit: int = 50):
        def check(m: discord.Message):
            return m.author.id == ctx.bot.user.id
        try:
            deleted = await ctx.channel.purge(limit=limit, check=check, bulk=True)
            await send_simple(ctx, "Cleaned", f"Deleted {len(deleted)} bot messages.", HELIX_SUCCESS)
        except discord.Forbidden:
            return await send_simple(ctx, "Permission Error", "I don't have permission to delete messages here.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Clean Failed", f"Error: `{e}`", HELIX_ERROR)

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, ctx: commands.Context, limit: int, mode: str = "any", *, value: Optional[str] = None):
        await ctx.trigger_typing()
        mode = (mode or "any").lower()
        if limit <= 0:
            return await send_simple(ctx, "Invalid limit", "Provide a positive number of messages to purge.", HELIX_WARN)
        check = None
        if mode == "any":
            check = None
        elif mode == "user":
            if not value:
                return await send_simple(ctx, "Missing argument", "When using `user` mode, give a user mention/ID/name.", HELIX_WARN)
            target = _resolve_member_by_query(ctx.guild, value)
            if not target:
                return await send_simple(ctx, "User not found", "Couldn't find that user.", HELIX_WARN)
            def check(m): return m.author.id == target.id
        elif mode == "contains":
            if not value:
                return await send_simple(ctx, "Missing argument", "When using `contains` mode, provide the text to match.", HELIX_WARN)
            def check(m): return value.lower() in (m.content or "").lower()
        else:
            return await send_simple(ctx, "Unknown mode", "Valid modes: any, user, contains", HELIX_WARN)
        try:
            deleted = await ctx.channel.purge(limit=limit, check=check, bulk=True)
            await send_simple(ctx, "Purged", f"Deleted {len(deleted)} messages.", HELIX_SUCCESS)
        except discord.Forbidden:
            return await send_simple(ctx, "Permission Error", "I don't have permission to delete messages here.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Purge Failed", f"Error: `{e}`", HELIX_ERROR)

    # ---------- slowmode / lock / unlock ----------
    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, delay: Optional[str] = None):
        if delay is None:
            current = ctx.channel.slowmode_delay
            return await send_simple(ctx, "Slowmode", f"Current slowmode: **{current}s**", HELIX_PRIMARY)
        if delay.lower() == "off":
            seconds = 0
        else:
            try:
                seconds = int(delay)
            except ValueError:
                return await send_simple(ctx, "Invalid", "Enter a number of seconds or `off`.", HELIX_WARN)
        try:
            await ctx.channel.edit(slowmode_delay=seconds, reason=f"Set by {ctx.author}")
            if seconds == 0:
                await send_simple(ctx, "Slowmode Disabled", f"Disabled in {ctx.channel.mention}.", HELIX_SUCCESS)
            else:
                await send_simple(ctx, "Slowmode Set", f"Set to **{seconds}s** in {ctx.channel.mention}.", HELIX_SUCCESS)
        except discord.Forbidden:
            return await send_simple(ctx, "Permission Error", "I can't manage this channel.", HELIX_ERROR)

    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *, reason: str = "No reason provided"):
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await send_simple(ctx, "Invalid Target", "Provide a text channel.", HELIX_WARN)
        overwrites = channel.overwrites_for(ctx.guild.default_role)
        if overwrites.send_messages is False:
            return await send_simple(ctx, "Already Locked", f"{channel.mention} is already locked.", HELIX_WARN)
        overwrites.send_messages = False
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=reason)
            await send_simple(ctx, "Locked", f"🔒 Locked {channel.mention}", HELIX_SUCCESS)
        except discord.Forbidden:
            return await send_simple(ctx, "Permission Error", "I cannot change channel permissions.", HELIX_ERROR)

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return await send_simple(ctx, "Invalid Target", "Provide a text channel.", HELIX_WARN)
        overwrites = channel.overwrites_for(ctx.guild.default_role)
        if overwrites.send_messages is True:
            return await send_simple(ctx, "Already Unlocked", f"{channel.mention} is already unlocked.", HELIX_WARN)
        overwrites.send_messages = True
        try:
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites, reason=f"Unlock by {ctx.author}")
            await send_simple(ctx, "Unlocked", f"🔓 Unlocked {channel.mention}", HELIX_SUCCESS)
        except discord.Forbidden:
            return await send_simple(ctx, "Permission Error", "I cannot change channel permissions.", HELIX_ERROR)

    # ---------- modstats (simple placeholder using modules['modstats']) ----------
    @commands.command(name="modstats", aliases=["ms"])
    @commands.has_permissions(manage_messages=True)
    async def modstats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        member = member or ctx.author
        async with AsyncSessionLocal() as session:
            cfg = await get_guild_cfg(session, ctx.guild.id)
            modstats = (cfg.modules or {}).get("modstats", {})
            their = modstats.get(str(member.id), {})
        if not their:
            return await send_simple(ctx, "Modstats", f"No moderation stats for {member.mention}.", HELIX_WARN)
        emb = mkembed(f"Modstats — {member}", color=HELIX_PRIMARY)
        actions = their.get("actions", [])
        emb.add_field(name="Actions", value=str(len(actions)), inline=False)
        for i, a in enumerate(reversed(actions[-5:]), 1):
            emb.add_field(name=f"{i}. {a.get('type')}", value=a.get("timestamp"), inline=False)
        emb.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
        await ctx.send(embed=emb)

    # ---------- role toggle ----------
    @commands.command(name="role")
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_cmd(self, ctx: commands.Context, user_query: str, *, role_name: str):
        target = None
        if ctx.message.mentions:
            target = ctx.message.mentions[0]
        else:
            # try MemberConverter first (supports ids, names)
            try:
                converter = commands.MemberConverter()
                target = await converter.convert(ctx, user_query)
            except Exception:
                target = _resolve_member_by_query(ctx.guild, user_query)
        if not target:
            return await send_simple(ctx, "User Not Found", "Could not find that user — try mention, ID, or full username.", HELIX_WARN)
        role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
        if not role:
            role = discord.utils.find(lambda r: role_name.lower() in r.name.lower(), ctx.guild.roles)
        if not role:
            return await send_simple(ctx, "Role Not Found", f"I couldn't find a role named `{role_name}`.", HELIX_WARN)
        bot_member = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
        if bot_member and role >= bot_member.top_role:
            return await send_simple(ctx, "Cannot Manage Role", "I cannot manage that role because it is equal or higher than my top role. Move my role above it.", HELIX_ERROR)
        if isinstance(ctx.author, discord.Member) and role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await send_simple(ctx, "Cannot Manage Role", "You can't manage a role equal or higher than your top role.", HELIX_WARN)
        try:
            if role in target.roles:
                await target.remove_roles(role, reason=f"Toggled off by {ctx.author}")
                emb = mkembed("🧩 Role Removed", f"Removed **{role.name}** from {target.mention}.", HELIX_SUCCESS)
                emb.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
                await ctx.send(embed=emb)
            else:
                await target.add_roles(role, reason=f"Toggled on by {ctx.author}")
                emb = mkembed("🧩 Role Added", f"Added **{role.name}** to {target.mention}.", HELIX_SUCCESS)
                emb.set_footer(text=FOOTER_TEXT, icon_url=(self.bot.user.display_avatar.url if getattr(self.bot.user,"display_avatar",None) else None))
                await ctx.send(embed=emb)
            await self._log_case(ctx, target, "Role Change", f"Toggled {role.name}", None, True)
        except discord.Forbidden:
            return await send_simple(ctx, "Forbidden", "I don't have permission to add/remove that role.", HELIX_ERROR)
        except Exception as e:
            return await send_simple(ctx, "Failed", f"Failed to update role: `{e}`", HELIX_ERROR)

# Cog setup
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
