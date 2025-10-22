from __future__ import annotations
import re
from typing import Optional, Tuple, List

import discord
from discord.ext import commands
from datetime import datetime, timezone

from cogs.core import mkembed, COLORS


HEX_RE = re.compile(r"^#?(?P<hex>[0-9a-fA-F]{6})$")

COMMON_USER_PERMS = {
    "view channel",
    "read message history",
    "send messages",
    "embed links",
    "attach files",
    "add reactions",
    "use external emojis",
    "use external stickers",
    "connect",
    "speak",
    "change nickname",
    "use voice activity",
}

def _infer_role_from_permissions(perms: discord.Permissions) -> str:
    """Guess the user's functional role in the server based on their permissions."""
    p = {name for name, val in perms if val}

    # Hierarchy of roles
    if "administrator" in p:
        return "Server Administrator 🛠️"
    if {"ban members", "kick members"} & p:
        return "Moderator 🔧"
    if {"manage messages", "mute members", "deafen members", "manage roles"} & p:
        return "Staff 🧩"
    if {"manage channels", "manage webhooks"} & p:
        return "Manager ⚙️"
    if {"manage emojis and stickers", "manage nicknames"} & p:
        return "Helper 🪄"
    if {"mention everyone", "create instant invite"} & p:
        return "Trusted Member 🌟"
    return "Member 👤"

def _prefix(ctx: commands.Context) -> str:
    if hasattr(ctx.bot, "prefix_cache") and ctx.guild:
        return ctx.bot.prefix_cache.get(str(ctx.guild.id), ";")
    return ";"


def _chunk_strs(tokens: List[str], max_len: int = 1000) -> List[str]:
    """Join tokens with spaces into chunks that fit within max_len."""
    chunks, cur = [], []
    for t in tokens:
        if len(" ".join(cur + [t])) > max_len:
            if cur:
                chunks.append(" ".join(cur))
            cur = [t]
        else:
            cur.append(t)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


async def _resolve_role(ctx: commands.Context, arg: Optional[str]) -> Optional[discord.Role]:
    if not ctx.guild or not arg:
        return None
    # mention / id
    m = re.search(r"(\d{15,25})", arg)
    rid = int(m.group(1)) if m else None
    if ctx.message.role_mentions:
        return ctx.message.role_mentions[0]
    if rid:
        role = ctx.guild.get_role(rid)
        if role:
            return role
    # name (case-insensitive exact, then contains)
    name_lc = arg.lower()
    by_exact = discord.utils.find(lambda r: r.name.lower() == name_lc, ctx.guild.roles)
    if by_exact:
        return by_exact
    by_contains = [r for r in ctx.guild.roles if name_lc in r.name.lower()]
    return by_contains[0] if by_contains else None


async def _resolve_member(ctx: commands.Context, arg: Optional[str]) -> Optional[discord.Member]:
    if not ctx.guild or not arg:
        return None
    # mention / id
    if ctx.message.mentions:
        u = ctx.message.mentions[0]
        m = ctx.guild.get_member(u.id)
        if m:
            return m
    m = re.search(r"(\d{15,25})", arg)
    uid = int(m.group(1)) if m else None
    if uid:
        member = ctx.guild.get_member(uid)
        if member:
            return member
        try:
            return await ctx.guild.fetch_member(uid)
        except Exception:
            return None
    # name search (best-effort)
    name_lc = arg.lower()
    return discord.utils.find(lambda mem: mem.name.lower() == name_lc or (mem.nick and mem.nick.lower() == name_lc), ctx.guild.members)


class Utility(commands.Cog):
    """Server utilities: roles, server/channel info, etc."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ===================== addrole =====================
    @commands.command(name="addrole")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def addrole(self, ctx: commands.Context, name: str, *flags: str):
        """
        Create a new role.
        Usage:
          ;addrole MyRole --color #ff8800 --hoist yes --mentionable no
        """
        # parse flags
        color = discord.Color.default()
        hoist = False
        mentionable = False

        it = list(flags)
        for i, token in enumerate(it):
            t = token.lower()
            if t in ("--color", "--colour") and i + 1 < len(it):
                m = HEX_RE.match(it[i + 1])
                if not m:
                    return await ctx.reply(embed=mkembed("❌ Invalid color", "Use a hex like `#ff8800`.", COLORS["ERROR"]))
                color = discord.Color(int(m.group("hex"), 16))
            elif t == "--hoist" and i + 1 < len(it):
                hoist = it[i + 1].lower() in ("yes", "true", "1", "y")
            elif t == "--mentionable" and i + 1 < len(it):
                mentionable = it[i + 1].lower() in ("yes", "true", "1", "y")

        # hierarchy check: bot must be able to create roles (implicit), placement auto-bottom
        try:
            role = await ctx.guild.create_role(
                name=name,
                colour=color,
                hoist=hoist,
                mentionable=mentionable,
                reason=f"Created by {ctx.author}",
            )
        except discord.Forbidden:
            return await ctx.reply(embed=mkembed("🔒 Blocked", "I can't create roles here (missing permission).", COLORS["ERROR"]))
        except discord.HTTPException as e:
            return await ctx.reply(embed=mkembed("❌ Failed to create role", f"`{type(e).__name__}: {e}`", COLORS["ERROR"]))

        em = mkembed("✅ Role Created",
                     f"Created role {role.mention}\nColor: `#{role.colour.value:06X}` • Hoist: `{role.hoist}` • Mentionable: `{role.mentionable}`",
                     COLORS["SUCCESS"])
        await ctx.reply(embed=em)

    # ===================== delrole =====================
    @commands.command(name="delrole")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def delrole(self, ctx: commands.Context, *, role_ref: str):
        """Delete a role by mention | id | name."""
        role = await _resolve_role(ctx, role_ref)
        if not role:
            return await ctx.reply(embed=mkembed("❌ Role Not Found", "I couldn't resolve that role.", COLORS["ERROR"]))

        bot_top = ctx.guild.me.top_role  # type: ignore
        if role >= bot_top:
            return await ctx.reply(embed=mkembed("🔒 Can't Delete",
                                                 f"{role.mention} is **higher than or equal to** my top role {bot_top.mention}.",
                                                 COLORS["WARNING"]))
        try:
            await role.delete(reason=f"Deleted by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(embed=mkembed("🔒 Blocked", "Discord prevented me from deleting that role.", COLORS["ERROR"]))
        except discord.HTTPException as e:
            return await ctx.reply(embed=mkembed("❌ Failed to delete role", f"`{type(e).__name__}: {e}`", COLORS["ERROR"]))

        await ctx.reply(embed=mkembed("✅ Role Deleted", f"Removed role **{role.name}**.", COLORS["SUCCESS"]))

    # ===================== role (toggle) =====================
    @commands.command(name="role")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role_toggle(self, ctx: commands.Context, user_ref: str, *, role_ref: str):
        """
        Toggle a role for a user.
        Usage:
          ;role @user @role
          ;role @user RoleName
          ;role @user 123456789012345678
        """
        member = await _resolve_member(ctx, user_ref)
        if not member:
            return await ctx.reply(embed=mkembed("❌ User Not Found", "I couldn't find that member in this server.", COLORS["ERROR"]))

        role = await _resolve_role(ctx, role_ref)
        if not role:
            return await ctx.reply(embed=mkembed("❌ Role Not Found", "I couldn't resolve that role.", COLORS["ERROR"]))

        bot_top = ctx.guild.me.top_role  # type: ignore
        if role >= bot_top:
            return await ctx.reply(embed=mkembed("🔒 Can't Manage Role",
                                                 f"{role.mention} is **higher than or equal to** my top role {bot_top.mention}.",
                                                 COLORS["WARNING"]))
        # Optional: invoker hierarchy hint
        inv_top = ctx.author.top_role
        if inv_top <= role and ctx.author.id != ctx.guild.owner_id:
            note = f"_FYI: Your top role {inv_top.mention} is not above {role.mention}. Proceeding if I can._"
        else:
            note = None

        try:
            if role in member.roles:
                await member.remove_roles(role, reason=f"Role toggle by {ctx.author}")
                action = "removed"
            else:
                await member.add_roles(role, reason=f"Role toggle by {ctx.author}")
                action = "assigned"
        except discord.Forbidden:
            return await ctx.reply(embed=mkembed("🔒 Blocked", "Discord blocked the role change (hierarchy or perms).", COLORS["ERROR"]))
        except discord.HTTPException as e:
            return await ctx.reply(embed=mkembed("❌ Failed", f"`{type(e).__name__}: {e}`", COLORS["ERROR"]))

        desc = f"{role.mention} **{action}** for {member.mention}."
        if note:
            desc += f"\n{note}"
        await ctx.reply(embed=mkembed("✅ Role Updated", desc, COLORS["SUCCESS"]))

    # ===================== rolesearch =====================
    @commands.command(name="rolesearch")
    @commands.guild_only()
    async def rolesearch(self, ctx: commands.Context, *, query: str):
        """
        Find roles whose names contain the given text (case-insensitive).
        Useful when you only recall part of a role's name. Shows matches and IDs.
        """
        q = query.strip().lower()
        matches = [r for r in ctx.guild.roles if q in r.name.lower()]
        if not matches:
            return await ctx.reply(embed=mkembed("🔎 Role Search", f"No roles found matching `{query}`.", COLORS["WARNING"]))

        # sort by position (top first)
        matches.sort(key=lambda r: r.position, reverse=True)
        lines = [f"{r.mention} — **{r.name}** (`{r.id}`)" for r in matches]

        em = mkembed("🔎 Role Search Results", f"Keyword: `{query}`\nMatches: **{len(matches)}**", COLORS["INFO"])
        for i, block in enumerate(_chunk_strs(lines, 950), 1):
            em.add_field(name=f"", value=block, inline=False)
        await ctx.reply(embed=em)

    # ===================== roleinfo =====================
    @commands.command(name="roleinfo")
    @commands.guild_only()
    async def roleinfo(self, ctx: commands.Context, *, role_ref: str):
        """Show detailed information about a role, including inferred status and key permissions."""
        role = await _resolve_role(ctx, role_ref)
        if not role:
            return await ctx.reply(embed=mkembed("❌ Role Not Found", "I couldn't resolve that role.", COLORS["ERROR"]))

        created_ts = int(role.created_at.replace(tzinfo=timezone.utc).timestamp())
        color_hex = f"#{role.colour.value:06X}"

        perms = role.permissions
        inferred = _infer_role_from_permissions(perms)

        allowed = [name.replace("_", " ").title() for name, val in perms if val]
        allowed = [p for p in allowed if p.lower() not in COMMON_USER_PERMS]
        allowed.sort()

        em = mkembed(f"🎭 Role Info — {role.name}", color=role.colour if role.colour.value else COLORS["INFO"])
        em.add_field(name="ID", value=str(role.id), inline=True)
        em.add_field(name="Position", value=str(role.position), inline=True)
        em.add_field(name="Color", value=color_hex, inline=True)
        em.add_field(name="Hoist", value=str(role.hoist), inline=True)
        em.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
        em.add_field(name="Managed", value=str(role.managed), inline=True)
        em.add_field(name="Members", value=str(sum(1 for m in ctx.guild.members if role in m.roles)), inline=True)
        em.add_field(name="Created", value=f"<t:{created_ts}:F> (<t:{created_ts}:R>)", inline=False)
        em.add_field(name="Significance:", value=inferred, inline=False)

        if allowed:
            allowed_chunks = _chunk_strs([f"`{p}`" for p in allowed], 950)
            for i, block in enumerate(allowed_chunks, 1):
                em.add_field(name=f"✅ Key Permissions:", value=block, inline=False)

        await ctx.reply(embed=em)

    # ===================== rolemembers =====================
    @commands.command(name="rolemembers")
    @commands.guild_only()
    async def rolemembers(self, ctx: commands.Context, *, role_ref: str):
        """List all members that have a given role."""
        role = await _resolve_role(ctx, role_ref)
        if not role:
            return await ctx.reply(embed=mkembed("❌ Role Not Found", "I couldn't resolve that role.", COLORS["ERROR"]))

        members = [m for m in ctx.guild.members if role in m.roles]
        members.sort(key=lambda m: (m.top_role.position, m.joined_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)

        if not members:
            return await ctx.reply(embed=mkembed("🎭 Role Members", f"No one currently has {role.mention}.", COLORS["INFO"]))

        tokens = [f"{m.mention} (`{m.id}`)" for m in members]
        em = mkembed(f"🎭 Members with {role.name}", f"Total: **{len(members)}**", COLORS["INFO"])
        for i, block in enumerate(_chunk_strs(tokens, 950), 1):
            em.add_field(name=f"", value=block, inline=False)
        await ctx.reply(embed=em)

    # ===================== serverinfo =====================
    @commands.command(name="serverinfo")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        g = ctx.guild
        created_ts = int(g.created_at.replace(tzinfo=timezone.utc).timestamp())
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        text_ch = len(g.text_channels)
        voice_ch = len(g.voice_channels)
        cats = len(g.categories)
        roles = len(g.roles)

        em = mkembed("🏠 Server Info", color=COLORS["INFO"])
        em.set_thumbnail(url=getattr(getattr(g, "icon", None), "url", None) or getattr(getattr(g, "icon", None), "url", None))
        em.add_field(name="Name", value=g.name, inline=True)
        em.add_field(name="ID", value=g.id, inline=True)
        em.add_field(name="Owner", value=getattr(g.owner, "mention", g.owner_id), inline=True)
        em.add_field(name="Members", value=f"Total: {g.member_count}\nHumans: {humans} • Bots: {bots}", inline=False)
        em.add_field(name="Channels", value=f"Text: {text_ch} • Voice: {voice_ch} • Categories: {cats}", inline=False)
        em.add_field(name="Roles", value=str(roles), inline=True)
        em.add_field(name="Created", value=f"<t:{created_ts}:F> (<t:{created_ts}:R>)", inline=False)
        await ctx.reply(embed=em)

    # ===================== icon =====================
    @commands.command(name="icon")
    @commands.guild_only()
    async def icon(self, ctx: commands.Context):
        g = ctx.guild
        icon = getattr(g, "icon", None)
        if not icon:
            return await ctx.reply("❌ This server has no icon set.")
        url_full = icon.replace(size=4096).url
        em = mkembed("🖼 Server Icon", color=COLORS["INFO"])
        em.set_image(url=url_full)
        await ctx.reply(embed=em)



    # ===================== channelinfo =====================
    @commands.command(name="channelinfo")
    @commands.guild_only()
    async def channelinfo(self, ctx: commands.Context, channel: Optional[discord.abc.GuildChannel] = None):
        """Show info about a channel (defaults to current)."""
        ch = channel or ctx.channel
        if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.CategoryChannel, discord.ForumChannel, discord.Thread)):
            return await ctx.reply(embed=mkembed("❌ Unsupported", "That channel type is not supported.", COLORS["ERROR"]))

        created_ts = int(ch.created_at.replace(tzinfo=timezone.utc).timestamp())
        em = mkembed(f"📺 Channel Info — #{getattr(ch, 'name', 'unknown')}", color=COLORS["INFO"])
        em.add_field(name="ID", value=str(ch.id), inline=True)
        em.add_field(name="Type", value=ch.__class__.__name__.replace("Channel", " Channel"), inline=True)
        em.add_field(name="Category", value=getattr(getattr(ch, 'category', None), 'name', "None"), inline=True)
        em.add_field(name="Position", value=str(getattr(ch, 'position', '—')), inline=True)
        em.add_field(name="Created", value=f"<t:{created_ts}:F> (<t:{created_ts}:R>)", inline=False)

        if isinstance(ch, discord.TextChannel):
            em.add_field(name="Topic", value=(ch.topic or "_None_")[:1024], inline=False)
            em.add_field(name="NSFW", value=str(ch.is_nsfw()), inline=True)
            em.add_field(name="Slowmode", value=f"{ch.slowmode_delay}s", inline=True)
            em.add_field(name="Threads", value=str(len(ch.threads)), inline=True)
        elif isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            if isinstance(ch, discord.VoiceChannel):
                em.add_field(name="Bitrate", value=f"{ch.bitrate} bps", inline=True)
                em.add_field(name="User Limit", value=str(ch.user_limit or "None"), inline=True)
            em.add_field(name="NSFW", value=str(getattr(ch, "nsfw", False)), inline=True)
        elif isinstance(ch, discord.ForumChannel):
            em.add_field(name="NSFW", value=str(ch.nsfw), inline=True)
            em.add_field(name="Threads", value=str(len(ch.threads)), inline=True)

        await ctx.reply(embed=em)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
