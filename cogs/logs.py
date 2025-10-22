from __future__ import annotations
import discord, contextlib
from discord.ext import commands
from datetime import datetime, timezone
from sqlalchemy import select, update
from db.engine import AsyncSessionLocal
from db.models import GuildConfig

COLORS = {
    "INFO": discord.Color.blurple(),
    "SUCCESS": discord.Color.green(),
    "WARNING": discord.Color.gold(),
    "ERROR": discord.Color.red(),
}

def mkembed(title: str, desc: str = "", color: discord.Color | None = None) -> discord.Embed:
    """Unified Helix embed style"""
    return discord.Embed(
        title=title,
        description=desc,
        color=color or COLORS["INFO"],
        timestamp=datetime.now(timezone.utc),
    )

# =============================
# Logs Cog
# =============================
class Logs(commands.Cog):
    """Helix logging system — logs moderation, roles, and user updates."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =============================
    # COMMANDS — set log channels
    # =============================
    @commands.group(name="log", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def log(self, ctx):
        await ctx.send(embed=mkembed("🪵 Logging Setup",
                                     "Use one of:\n"
                                     "`;log moderation #channel`\n"
                                     "`;log roles #channel`\n"
                                     "`;log server #channel`\n"
                                     "`;log profile #channel`",
                                     COLORS["INFO"]))

    @log.command(name="moderation")
    @commands.has_permissions(manage_guild=True)
    async def log_moderation(self, ctx, channel: discord.TextChannel):
        await self._set_log_channel(ctx, "mod_log_channel", channel)

    @log.command(name="roles")
    @commands.has_permissions(manage_guild=True)
    async def log_roles(self, ctx, channel: discord.TextChannel):
        await self._set_log_channel(ctx, "role_log_channel", channel)

    @log.command(name="server")
    @commands.has_permissions(manage_guild=True)
    async def log_server(self, ctx, channel: discord.TextChannel):
        await self._set_log_channel(ctx, "server_log_channel", channel)

    @log.command(name="profile")
    @commands.has_permissions(manage_guild=True)
    async def log_profile(self, ctx, channel: discord.TextChannel):
        await self._set_log_channel(ctx, "profile_log_channel", channel)

    # =============================
    # INTERNAL DB HANDLERS
    # =============================
    async def _set_log_channel(self, ctx, key: str, channel: discord.TextChannel):
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == str(ctx.guild.id)))
            cfg = res.scalar_one_or_none()
            if not cfg:
                cfg = GuildConfig(guild_id=str(ctx.guild.id), prefix=";", modules={})
                session.add(cfg)
                await session.commit()

            modules = cfg.modules or {}
            modules[key] = channel.id
            cfg.modules = modules
            session.add(cfg)
            await session.commit()

        await ctx.send(embed=mkembed("✅ Log Channel Set",
                                     f"{key.replace('_', ' ').title()} set to {channel.mention}",
                                     COLORS["SUCCESS"]))

    async def _get_channel(self, guild: discord.Guild, key: str) -> discord.TextChannel | None:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == str(guild.id)))
            cfg = res.scalar_one_or_none()
            if not cfg or not cfg.modules:
                return None
            ch_id = cfg.modules.get(key)
            if not ch_id:
                return None
            return guild.get_channel(ch_id)

    # =============================
    # EVENT LISTENERS
    # =============================
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ch = await self._get_channel(member.guild, "server_log_channel")
        if not ch: return
        emb = mkembed("👋 Member Joined",
                      f"{member.mention} ({member}) joined the server.",
                      COLORS["SUCCESS"])
        emb.set_thumbnail(url=member.display_avatar.url)
        await ch.send(embed=emb)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        ch = await self._get_channel(member.guild, "server_log_channel")
        if not ch: return
        emb = mkembed("👋 Member Left",
                      f"{member.mention} ({member}) left the server.",
                      COLORS["WARNING"])
        emb.set_thumbnail(url=member.display_avatar.url)
        await ch.send(embed=emb)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # nickname change
        if before.nick != after.nick:
            ch = await self._get_channel(after.guild, "profile_log_channel")
            if ch:
                emb = mkembed("📝 Nickname Updated",
                              f"{after.mention}\n**Before:** {before.nick}\n**After:** {after.nick}",
                              COLORS["INFO"])
                await ch.send(embed=emb)

        # role changes
        if before.roles != after.roles:
            ch = await self._get_channel(after.guild, "role_log_channel")
            if ch:
                added = [r.mention for r in after.roles if r not in before.roles and r.name != "@everyone"]
                removed = [r.mention for r in before.roles if r not in after.roles and r.name != "@everyone"]
                desc = []
                if added: desc.append(f"✅ **Added:** {', '.join(added)}")
                if removed: desc.append(f"❌ **Removed:** {', '.join(removed)}")
                if not desc: return
                emb = mkembed("🎭 Role Update", "\n".join(desc), COLORS["INFO"])
                emb.set_author(name=str(after), icon_url=after.display_avatar.url)
                await ch.send(embed=emb)

        # avatar changes
        if before.display_avatar.url != after.display_avatar.url:
            ch = await self._get_channel(after.guild, "profile_log_channel")
            if ch:
                emb = mkembed("🖼 Avatar Changed", f"{after.mention} changed their profile picture.", COLORS["INFO"])
                emb.set_image(url=after.display_avatar.url)
                await ch.send(embed=emb)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        ch = await self._get_channel(message.guild, "server_log_channel")
        if not ch: return
        emb = mkembed("🗑️ Message Deleted",
                      f"**Author:** {message.author.mention}\n"
                      f"**Channel:** {message.channel.mention}\n\n"
                      f"**Content:**\n{message.content or '*[No content]*'}",
                      COLORS["WARNING"])
        await ch.send(embed=emb)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild or before.content == after.content:
            return
        ch = await self._get_channel(before.guild, "server_log_channel")
        if not ch: return
        emb = mkembed("✏️ Message Edited",
                      f"**Author:** {before.author.mention}\n"
                      f"**Channel:** {before.channel.mention}\n\n"
                      f"**Before:** {before.content}\n**After:** {after.content}",
                      COLORS["INFO"])
        await ch.send(embed=emb)

    # This allows your moderation cog to call log messages too:
    async def log_moderation_action(self, guild: discord.Guild, action: str, user: discord.Member | discord.User, moderator: discord.Member, reason: str = "No reason provided"):
        ch = await self._get_channel(guild, "mod_log_channel")
        if not ch: return
        emb = mkembed(f"⚖️ {action.title()}",
                      f"**User:** {user.mention}\n"
                      f"**Moderator:** {moderator.mention}\n"
                      f"**Reason:** {reason}",
                      COLORS["INFO"])
        emb.set_thumbnail(url=user.display_avatar.url)
        await ch.send(embed=emb)


# =============================
# Setup
# =============================
async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))
