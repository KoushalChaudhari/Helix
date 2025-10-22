# cogs/userinfo.py
from __future__ import annotations
import re
import contextlib
from typing import Optional, Tuple
from datetime import datetime, timezone

import discord
from discord.ext import commands

# === Helix color scheme & embed ===
HELIX_PRIMARY = discord.Color.from_rgb(110, 82, 255)
HELIX_SUCCESS = discord.Color.from_rgb(60, 180, 150)
HELIX_WARN = discord.Color.gold()
HELIX_ERROR = discord.Color.from_rgb(255, 85, 160)
FOOTER_TEXT = "💠 Helix User Info"

def mkembed(title: str, desc: str = "", color: discord.Color = HELIX_PRIMARY) -> discord.Embed:
    emb = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(timezone.utc))
    emb.set_footer(text=FOOTER_TEXT)
    return emb

PROFILE_URL_RE = re.compile(r"discord(?:app)?\.com/users/(?P<id>\d{15,25})", re.I)


# === Helper: resolve user/member ===
async def _resolve_user_member(ctx: commands.Context, arg: Optional[str]) -> Tuple[Optional[discord.User], Optional[discord.Member]]:
    if not arg:
        user = ctx.author
        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        return user, member

    if ctx.message.mentions:
        u = ctx.message.mentions[0]
        return u, ctx.guild.get_member(u.id) if ctx.guild else None

    m = PROFILE_URL_RE.search(arg)
    if m:
        uid = int(m.group("id"))
        try:
            user = await ctx.bot.fetch_user(uid)
            member = ctx.guild.get_member(uid) if ctx.guild else None
            return user, member
        except Exception:
            return None, None

    m = re.search(r"(\d{15,25})", arg)
    if m:
        uid = int(m.group(1))
        user = ctx.bot.get_user(uid)
        if not user:
            with contextlib.suppress(Exception):
                user = await ctx.bot.fetch_user(uid)
        member = ctx.guild.get_member(uid) if ctx.guild else None
        return user, member

    if ctx.guild:
        member = discord.utils.find(lambda mem: mem.name.lower() == arg.lower(), ctx.guild.members)
        if member:
            return member, member

    return None, None


# === Cog ===
class UserInfo(commands.Cog):
    """User/Profile utilities: userinfo, avatar, banner, nick, id"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- userinfo ---
    @commands.command(name="userinfo", aliases=["whois", "ui"])
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def userinfo(self, ctx: commands.Context, target: Optional[str] = None):
        user, member = await _resolve_user_member(ctx, target)
        if not user:
            return await ctx.reply("❌ User not found. Try mention, ID, or profile URL.")

        with contextlib.suppress(Exception):
            user = await ctx.bot.fetch_user(user.id)

        flags_text = []
        pf = getattr(user, "public_flags", None)
        if pf:
            for name, val in vars(pf).items():
                if val:
                    flags_text.append(name.replace("_", " ").title())

        created_ts = int(user.created_at.replace(tzinfo=timezone.utc).timestamp())
        joined_ts = int(member.joined_at.replace(tzinfo=timezone.utc).timestamp()) if member and member.joined_at else None

        desc = [
            f"**ID:** `{user.id}`",
            f"**Bot:** {'Yes' if user.bot else 'No'}",
            f"**Created:** <t:{created_ts}:R>",
        ]
        if joined_ts:
            desc.append(f"**Joined:** <t:{joined_ts}:R>")
        if flags_text:
            desc.append(f"**Badges:** {', '.join(flags_text)}")

        color = member.color if member and member.color.value else HELIX_PRIMARY
        embed = mkembed(f"👤 {user}", "\n".join(desc), color)

        with contextlib.suppress(Exception):
            embed.set_thumbnail(url=user.display_avatar.url)

        if member:
            roles = [r for r in member.roles if r.id != ctx.guild.id]
            if roles:
                roles.sort(key=lambda r: r.position, reverse=True)
                role_text = " ".join(r.mention for r in roles)
                if len(role_text) > 1000:
                    role_text = role_text[:1000] + "..."
                embed.add_field(name=f"🎭 Roles ({len(roles)})", value=role_text, inline=False)
            else:
                embed.add_field(name="🎭 Roles", value="_No roles_", inline=False)
        else:
            embed.add_field(name="Note", value="*(User not in this server)*", inline=False)

        await ctx.reply(embed=embed)

    # --- avatar ---
    @commands.command(name="avatar", aliases=["av", "pfp"])
    @commands.bot_has_permissions(embed_links=True)
    async def avatar(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        if not user:
            return await ctx.reply("❌ User not found.")
        asset = user.display_avatar.replace(size=4096)
        embed = mkembed(f"🖼 Avatar — {user}", "", HELIX_PRIMARY)
        embed.set_image(url=asset.url)
        await ctx.reply(embed=embed)

    # --- banner ---
    @commands.command(name="banner", aliases=["bn"])
    @commands.bot_has_permissions(embed_links=True)
    async def banner(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        with contextlib.suppress(Exception):
            user = await ctx.bot.fetch_user(user.id)
        banner = getattr(user, "banner", None)
        if not banner:
            return await ctx.reply("❌ That user doesn’t have a visible banner.")
        url = banner.replace(size=4096).url if hasattr(banner, "replace") else banner.url
        embed = mkembed(f"🖼 Banner — {user}", "", HELIX_PRIMARY)
        embed.set_image(url=url)
        await ctx.reply(embed=embed)

    # --- nick ---
    @commands.command(name="nick")
    @commands.guild_only()
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick(self, ctx: commands.Context, target: Optional[str] = None, *, new: Optional[str] = None):
        if not target or new is None:
            return await ctx.reply(f"Usage: `{self._prefix(ctx)}nick @user <new>`")

        _, member = await _resolve_user_member(ctx, target)
        if not member:
            return await ctx.reply(embed=mkembed("❌ User Not Found", "Member not in this server.", HELIX_ERROR))
        if member == ctx.author:
            return await ctx.reply(embed=mkembed("❌ Not Allowed", "You can’t change your own nickname.", HELIX_ERROR))
        if len(new) > 32 or not new.strip():
            return await ctx.reply(embed=mkembed("❌ Invalid Nick", "Nickname must be 1–32 characters.", HELIX_ERROR))

        bot_member = ctx.guild.me
        if member.top_role >= bot_member.top_role:
            return await ctx.reply(embed=mkembed("🔒 Cannot Edit", "That member’s top role is above mine.", HELIX_WARN))

        try:
            await member.edit(nick=new.strip(), reason=f"Changed by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(embed=mkembed("🔒 Blocked", "I lack permission to change that nickname.", HELIX_ERROR))
        except Exception as e:
            return await ctx.reply(embed=mkembed("❌ Failed", str(e), HELIX_ERROR))

        await ctx.reply(embed=mkembed("✅ Nickname Updated", f"{member.mention} → **{discord.utils.escape_markdown(new)}**", HELIX_SUCCESS))

    # --- id ---
    @commands.command(name="id")
    async def _id(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        if not user:
            return await ctx.reply("❌ User not found.")
        await ctx.reply(f"### {user}\n```{user.id}```")

    def _prefix(self, ctx: commands.Context) -> str:
        if hasattr(ctx.bot, "prefix_cache") and ctx.guild:
            return ctx.bot.prefix_cache.get(str(ctx.guild.id), ";")
        return ";"


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
