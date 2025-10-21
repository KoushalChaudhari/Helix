# cogs/userinfo.py
from __future__ import annotations
import re
from typing import Optional, Tuple

import discord
from discord.ext import commands
from datetime import datetime, timezone

# Reuse your existing embed style/colors
from cogs.core import mkembed, COLORS

PROFILE_URL_RE = re.compile(r"discord(?:app)?\.com/users/(?P<id>\d{15,25})", re.I)

async def _resolve_user_member(ctx: commands.Context, arg: Optional[str]) -> Tuple[Optional[discord.User], Optional[discord.Member]]:
    """
    Resolve a target by:
      - @mention
      - raw ID
      - profile URL: https://discord.com/users/<id>
    Falls back to author if arg is None.
    Returns (User|None, Member|None).
    """
    # default to author
    if not arg:
        user = ctx.author
        member = ctx.author if isinstance(ctx.author, discord.Member) else (ctx.guild.get_member(ctx.author.id) if ctx.guild else None)
        return user, member

    # 1) mention in message?
    if ctx.message.mentions:
        u = ctx.message.mentions[0]
        m = ctx.guild.get_member(u.id) if ctx.guild else None
        return u, m

    # 2) profile URL
    m = PROFILE_URL_RE.search(arg)
    if m:
        uid = int(m.group("id"))
        try:
            user = await ctx.bot.fetch_user(uid)
            member = None
            if ctx.guild:
                member = ctx.guild.get_member(uid) or await ctx.guild.fetch_member(uid)
            return user, member
        except Exception:
            return None, None

    # 3) raw ID in text
    m = re.search(r"(\d{15,25})", arg)
    if m:
        uid = int(m.group(1))
        try:
            user = await ctx.bot.fetch_user(uid)  # works globally
        except Exception:
            # last resort: maybe cached
            user = ctx.bot.get_user(uid)
        member = None
        if ctx.guild and uid:
            member = ctx.guild.get_member(uid)
            if member is None:
                with contextlib.suppress(Exception):
                    member = await ctx.guild.fetch_member(uid)
        return user, member

    # 4) fallback: try by name only if in this guild/cache
    if ctx.guild:
        member = discord.utils.find(lambda mem: mem.name.lower() == arg.lower(), ctx.guild.members)
        if member:
            return member._user if hasattr(member, "_user") else member, member  # type: ignore

    # Could not resolve globally without an ID
    return None, None


class UserInfo(commands.Cog):
    """User/Profile utilities: whois, avatar, banner, nick, roles, id"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot





# ==========================================
#              Userinfo Command
# ==========================================
    @commands.command(name="userinfo", aliases=["whois", "ui"])
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def userinfo(self, ctx: commands.Context, target: Optional[str] = None):
        try:
            user, member = await _resolve_user_member(ctx, target)
        except Exception:
            return await ctx.reply("❌ User not found.")

        if not user:
             return await ctx.reply("❌ User not found. Tip: use a **mention**, **ID**, or a **profile URL** like `https://discord.com/users/<id>`.")

        # Try refreshing data (for banner, flags, etc.)
        try:
            user = await ctx.bot.fetch_user(user.id)
        except Exception:
            pass

        # Collect badges (public flags)
        flags_text = []
        try:
            pf = getattr(user, "public_flags", None)
            if pf:
                for name, val in vars(pf).items():
                    if val is True:
                        flags_text.append(name.replace("_", " ").title())
        except Exception:
            pass

        created_ts = int(user.created_at.replace(tzinfo=timezone.utc).timestamp())

        # Member-only info (joined date, roles)
        joined_str = None
        if member and member.joined_at:
            joined_ts = int(member.joined_at.replace(tzinfo=timezone.utc).timestamp())
            joined_str = f"<t:{joined_ts}:R>"

        color = member.colour if isinstance(member, discord.Member) and member.colour.value else COLORS["INFO"]

        # Base details
        lines = [
            f"**ID:** `{user.id}`",
            f"**Bot:** {'Yes' if user.bot else 'No'}",
            f"**Created:** <t:{created_ts}:R>",
        ]
        if joined_str:
            lines.append(f"**Joined:** {joined_str}")
        if flags_text:
            lines.append(f"**Badges:** {', '.join(flags_text)}")

        embed = mkembed(
            title=f"👤 {user}",
            desc="\n".join(lines),
            color=color,
        )

        try:
            embed.set_thumbnail(url=user.display_avatar.url)
        except Exception:
            pass

        # 🧩 Roles (only for members of this guild)
        if member and ctx.guild:
            roles = [r for r in member.roles if r.id != ctx.guild.id]
            roles.sort(key=lambda r: r.position, reverse=True)

            if not roles:
                embed.add_field(name="🎭 Roles", value="_No roles_", inline=False)
            else:
                role_mentions = [f"<@&{r.id}>" for r in roles]
                # Split into safe 1024-char chunks
                chunks, cur = [], []
                for token in role_mentions:
                    if len(" ".join(cur + [token])) > 1000:
                        chunks.append(" ".join(cur))
                        cur = [token]
                    else:
                        cur.append(token)
                if cur:
                    chunks.append(" ".join(cur))

                embed.add_field(name="🎭 Roles", value=f"Total: **{len(roles)}**", inline=False)
                for i, block in enumerate(chunks, 1):
                    embed.add_field(name=f"Set {i}" if len(chunks) > 1 else "List", value=block, inline=False)

        # ✅ If user not in server (member is None)
        elif not member and target:
            embed.add_field(name="Note", value="*(User is not in this server — showing global info only.)*", inline=False)

        await ctx.reply(embed=embed)





# ==========================================
#               Avatar Command
# ==========================================
    @commands.command(name="avatar", aliases=["av", "pfp"])
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def avatar(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        asset = user.display_avatar
        base = asset.replace(size=4096)
        # Try to present a main link + format alts
        url_main = base.url
        alts = []
        for ext in ("png", "jpg", "webp", "gif"):
            try:
                alts.append(f"[{ext.upper()}]({asset.with_format(ext).replace(size=1024).url})")
            except Exception:
                pass

        embed = mkembed(
            title=f"🖼 Avatar — {user}",
            desc=(" | ".join(alts)) or "",
            color=COLORS["INFO"],
        )
        try:
            embed.set_image(url=url_main)
        except Exception:
            pass

        await ctx.reply(embed=embed)





# ==========================================
#               Banner Command
# ==========================================
    @commands.command(name="banner", aliases=["bn"])
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def banner(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        # Need fetch_user to populate banner
        try:
            user = await ctx.bot.fetch_user(user.id)
        except Exception:
            pass

        banner = getattr(user, "banner", None)
        if not banner:
            return await ctx.reply("❌ That user doesn’t have a profile banner (or it’s not visible).")

        try:
            url = banner.replace(size=4096).url
        except Exception:
            url = banner.url

        embed = mkembed(
            title=f"🖼 Banner — {user}",
            color=COLORS["INFO"],
        )
        embed.set_image(url=url)
        await ctx.reply(embed=embed)

# ==========================================
#              Nick Command
# ==========================================
    @commands.command(name="nick")
    @commands.guild_only()
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick(self, ctx: commands.Context, target: Optional[str] = None, *, new: Optional[str] = None):
        # Caller must have Manage Nicknames
        if not ctx.author.guild_permissions.manage_nicknames:
            return await ctx.reply(embed=mkembed(
                "❌ No Permission",
                "You need **Manage Nicknames** to do that.",
                COLORS["ERROR"]
            ))

        # Usage
        if not target or new is None:
            return await ctx.reply(f"Usage: `{_prefix(ctx)}nick @user <new>`")

        # Resolve target in this guild
        _, member = await _resolve_user_member(ctx, target)
        if not member:
            return await ctx.reply(embed=mkembed(
                "❌ User Not Found",
                "I couldn’t find that member **in this server**.",
                COLORS["ERROR"]
            ))

        # Guardrails
        if member == ctx.author:
            return await ctx.reply(embed=mkembed(
                "❌ Not Allowed",
                "You can’t change **your own** nickname with this command.",
                COLORS["ERROR"]
            ))

        # Prepare some role info
        bot_member: discord.Member = ctx.guild.me  # type: ignore
        bot_top = bot_member.top_role
        tgt_top = member.top_role
        inv_top = ctx.author.top_role

        # Check bot’s ability (role hierarchy)
        if tgt_top >= bot_top:
            return await ctx.reply(embed=mkembed(
                "🔒 I Can’t Edit That Nickname",
                (
                    f"The member’s top role {tgt_top.mention} is **higher than or equal to** my top role "
                    f"{bot_top.mention}, so Discord prevents me from changing it.\n\n"
                    "• Ask an admin to move my bot role **above** the target’s top role.\n"
                    f"• Then run `{_prefix(ctx)}nick @user <new>` again."
                ),
                COLORS["WARNING"]
            ))

        # Optional: check the invoker’s hierarchy (nice UX hint)
        if inv_top <= tgt_top and ctx.author.id != ctx.guild.owner_id:
            # They have permission flag but lower/equal role than target; Discord would block them if they tried themselves.
            # The bot *can* still change if its role is high enough, but give a polite heads-up.
            # (We still proceed if the bot can manage, just inform.)
            note = (
                f"FYI: Your top role {inv_top.mention} is **not above** the member’s top role {tgt_top.mention}. "
                "I’ll attempt it since **my** role is high enough."
            )
        else:
            note = None

        # Validate nickname
        new = new.strip()
        if not new:
            return await ctx.reply(embed=mkembed(
                "❌ Invalid Nickname",
                "Please provide a **non-empty** nickname.",
                COLORS["ERROR"]
            ))
        if len(new) > 32:
            return await ctx.reply(embed=mkembed(
                "❌ Too Long",
                "Discord limits nicknames to **32 characters**.",
                COLORS["ERROR"]
            ))

        # Try the edit
        try:
            await member.edit(nick=new, reason=f"Nick changed by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(embed=mkembed(
                "🔒 Blocked by Discord",
                "I don’t have permission to change that member’s nickname (role hierarchy or missing perms).",
                COLORS["ERROR"]
            ))
        except discord.HTTPException as e:
            return await ctx.reply(embed=mkembed(
                "❌ Failed to Change Nickname",
                f"`{type(e).__name__}: {e}`",
                COLORS["ERROR"]
            ))

        # Success
        desc = f"{member.mention} → **{discord.utils.escape_markdown(new)}**"
        if note:
            desc += f"\n\n_{note}_"
        await ctx.reply(embed=mkembed("✅ Nickname Updated", desc, COLORS["SUCCESS"]))





# ==========================================
#              user ID Command
# ==========================================
    @commands.command(name="id")
    async def _id(self, ctx: commands.Context, target: Optional[str] = None):
        user, _ = await _resolve_user_member(ctx, target)
        await ctx.reply(f"### {user} ``` {user.id} ```")


def _prefix(ctx: commands.Context) -> str:
    if hasattr(ctx.bot, "prefix_cache") and ctx.guild:
        return ctx.bot.prefix_cache.get(str(ctx.guild.id), ";")
    return ";"


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
