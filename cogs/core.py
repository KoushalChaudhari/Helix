from __future__ import annotations
import json, os, contextlib
import platform, sys, pkgutil
import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import importlib.util
try:
    import psutil  
except Exception as e:
    psutil = None
from sqlalchemy import select
from db.engine import AsyncSessionLocal
from db.models import GuildConfig
from discord.ext.commands import Command, Group, BucketType
try:
    from config import FEEDBACK_CHANNEL_ID
except Exception:
    FEEDBACK_CHANNEL_ID = int(os.getenv("FEEDBACK_CHANNEL_ID", "0") or 0)
try:
    from config import BUG_CHANNEL_ID
except Exception:
    BUG_CHANNEL_ID = int(os.getenv("BUG_CHANNEL_ID", "0") or 0)


BOT_VERSION = "1.0.0"
DEV_NAME = "<@555982190639579144> , <@1128018348119183410>"

COLORS = {
    "INFO": discord.Color.blurple(),
    "SUCCESS": discord.Color.green(),
    "WARNING": discord.Color.gold(),
    "ERROR": discord.Color.red(),
}


HELP_JSON_PATH = os.path.join(os.path.dirname(__file__), "help_descriptions.json")


# ==========================================
#              Helper functions
# ==========================================
def mkembed(title: str, desc: str = "", color: discord.Color | None = None) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=desc,
        color=color or COLORS["INFO"],
        timestamp=datetime.now(timezone.utc),
    )

def humanize_tdelta(td: timedelta) -> str:
    secs = int(td.total_seconds())
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if secs >= size:
            n, secs = divmod(secs, size)
            parts.append(f"{n}{label}")
    return " ".join(parts) if parts else "0s"

def humanize_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def _uptime_str(bot) -> str:
    boot = getattr(bot, "boot_time", datetime.now(timezone.utc))
    delta = datetime.now(timezone.utc) - boot
    secs = int(delta.total_seconds())
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if secs >= size:
            n, secs = divmod(secs, size)
            parts.append(f"{n}{label}")
    return " ".join(parts) or "0s"

async def _get_guild_cfg(session, guild_id: str) -> GuildConfig:
    res = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = GuildConfig(guild_id=guild_id, prefix=";", modules={})
        session.add(cfg)
        await session.commit()
    if cfg.modules is None:
        cfg.modules = {}
    return cfg



# ==================START===================
class Core(commands.Cog):
    """Core commands: ping, help, uptime, etc. (we’ll add them one-by-one)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not hasattr(self.bot, "boot_time"):
            from datetime import datetime, timezone
            self.bot.boot_time = datetime.now(timezone.utc)
        self.help_index = self._load_help_index()

    def _load_help_index(self) -> dict:
            try:
                with open(HELP_JSON_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # minimal shape guard
                    if not isinstance(data, dict):
                        raise ValueError("help_descriptions.json is not a JSON object")
                    data.setdefault("categories", {})
                    data.setdefault("commands", {})
                    return data
            except FileNotFoundError:
                print(f"[HELP] File not found: {HELP_JSON_PATH}")
            except Exception as e:
                print(f"[HELP] Failed to load JSON: {e}")
            return {"categories": {}, "commands": {}}

    def _prefix(self, ctx: commands.Context) -> str:
        if hasattr(self.bot, "prefix_cache") and ctx.guild:
            return self.bot.prefix_cache.get(str(ctx.guild.id), ";")
        return ";"


    def __init__(self, bot: commands.Bot):
        self.bot = bot

        if not hasattr(self.bot, "boot_time"):
            self.bot.boot_time = datetime.now(timezone.utc)



# ==========================================
#                Ping command
# ==========================================
    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        """Show current websocket latency."""
        # discord.py latency is a float (seconds). Convert to ms and format nicely.
        ws_ms = round(self.bot.latency * 1000, 2)
        embed = mkembed(
            title="🏓 Pong!",
            desc=f"WebSocket latency: **{ws_ms} ms**",
            color=COLORS["SUCCESS"] if ws_ms < 200 else COLORS["WARNING"] if ws_ms < 400 else COLORS["ERROR"],
        )
        await ctx.send(embed=embed)




# ==========================================
#                Uptime command
# ==========================================
    @commands.command(name="uptime")
    async def uptime(self, ctx: commands.Context):
        """Show how long the bot has been online."""
        boot: datetime = getattr(self.bot, "boot_time", datetime.now(timezone.utc))
        delta = datetime.now(timezone.utc) - boot
        pretty = humanize_tdelta(delta)

        embed = mkembed(
            title="⏱ Uptime",
            desc=f"Online for **{pretty}**\nSince: <t:{int(boot.timestamp())}:F>",
            color=COLORS["INFO"],
        )
        await ctx.send(embed=embed)



# ==========================================
#                Invite command
# ==========================================
    @commands.command(name="invite")
    async def invite(self, ctx: commands.Context):
        """Get the bot's invite link with Administrator permissions."""
        # Determine application/client ID
        app_id = getattr(self.bot, "application_id", None) or (
            self.bot.user.id if self.bot.user else None
        )

        if not app_id:
            embed = mkembed(
                title="🔗 Invite",
                desc="I couldn’t determine my Application ID right now. Please try again shortly.",
                color=COLORS["ERROR"],
            )
            return await ctx.send(embed=embed)

        # Admin invite (permissions=8)
        base = "https://discord.com/oauth2/authorize"
        scopes = "bot%20applications.commands"
        url_admin = f"{base}?client_id={app_id}&scope={scopes}&permissions=8"

        # Embed + button
        embed = mkembed(
            title="🤖 Invite the Bot",
            desc=(
                f"Click the button below to invite **{self.bot.user.name}** "
                f"to your server with **Administrator** permissions.\n\n"
                f"Permissions: `Administrator (8)`\n"
                f"Scopes: `bot`, `applications.commands`"
            ),
            color=COLORS["INFO"],
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Invite Me", url=url_admin))

        await ctx.send(embed=embed, view=view)




# ==========================================
#                About command
# ==========================================
    @commands.command(name="about")
    async def about(self, ctx: commands.Context):
        """Show information about the bot."""
        bot = self.bot
        app_id = getattr(bot, "application_id", None) or (bot.user.id if bot.user else None)

        # Build invite link (admin perms)
        invite_url = None
        if app_id:
            base = "https://discord.com/oauth2/authorize"
            scopes = "bot%20applications.commands"
            invite_url = f"{base}?client_id={app_id}&scope={scopes}&permissions=8"

        # Safe discord.py version detection
        spec = importlib.util.find_spec("discord")
        if spec and spec.loader:
            libver = getattr(discord, "__version__", "N/A")
        else:
            libver = "N/A"

        ws_ms = round(bot.latency * 1000, 2)
        guilds = len(bot.guilds)
        members = sum(g.member_count or 0 for g in bot.guilds)
        pyver = platform.python_version()
        uptime = _uptime_str(bot)

        desc = (
            f"**{bot.user.name}** — Jack of All Trades\n"
            f"Version: `{BOT_VERSION}` • Developer: **{DEV_NAME}**\n\n"
            f"**Servers:** {guilds}\n"
            f"**Members (approx):** {members}\n"
            f"**Latency:** {ws_ms} ms\n"
            f"**Uptime:** {uptime}\n"
            f"**Library:** discord.py `{libver}` • Python `{pyver}`"
        )

        embed = mkembed(title="ℹ️ About This Bot", desc=desc, color=COLORS["INFO"])
        embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user and bot.user.display_avatar else discord.Embed.Empty)

        view = None
        if invite_url:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Invite (Admin)", url=invite_url))

        await ctx.send(embed=embed)
        if view:
            await ctx.send(view=view)


# ==========================================
#                Stats command
# ==========================================
    @commands.command(name="stats")
    async def stats(self, ctx: commands.Context):
        """Show bot/system statistics."""

        def humanize_bytes(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024:
                    return f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} PB"

        try:
            bot = self.bot

            guilds = len(bot.guilds)
            members = sum((g.member_count or 0) for g in bot.guilds)
            text_channels = sum(len(getattr(g, "text_channels", [])) for g in bot.guilds)
            voice_channels = sum(len(getattr(g, "voice_channels", [])) for g in bot.guilds)
            total_channels = text_channels + voice_channels
            commands_loaded = len(bot.commands)

            import platform, importlib.util
            pyver = platform.python_version()
            spec = importlib.util.find_spec("discord")
            libver = getattr(discord, "__version__", "N/A") if spec and spec.loader else "N/A"

            ws_ms = round(bot.latency * 1000, 2)
            boot: datetime = getattr(bot, "boot_time", datetime.now(timezone.utc))
            delta = datetime.now(timezone.utc) - boot

            def _humanize(td: timedelta) -> str:
                secs = int(td.total_seconds())
                parts = []
                for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
                    if secs >= size:
                        n, secs = divmod(secs, size)
                        parts.append(f"{n}{label}")
                return " ".join(parts) if parts else "0s"

            uptime_str = _humanize(delta)

            cpu_str, ram_str = "N/A", "N/A"
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory()
                proc = psutil.Process()
                rss = proc.memory_info().rss
                cpu_str = f"{cpu:.0f}%"
                ram_str = f"{humanize_bytes(rss)} / {humanize_bytes(mem.total)}"
            except Exception as e:
                print("[DEBUG] psutil not available or failed:", repr(e))

            color = COLORS["SUCCESS"] if ws_ms < 200 else COLORS["WARNING"] if ws_ms < 400 else COLORS["ERROR"]

            print("[DEBUG] mkembed output:", type(mkembed("test", "body")))

            # Try EMBED first
            try:
                embed = mkembed(title="📊 Bot Statistics", color=color)
                embed.add_field(name="Servers", value=str(guilds), inline=True)
                embed.add_field(name="Members (approx)", value=str(members), inline=True)
                embed.add_field(name="Channels", value=f"{total_channels} (T:{text_channels} / V:{voice_channels})", inline=True)
                embed.add_field(name="Commands Loaded", value=str(commands_loaded), inline=True)
                embed.add_field(name="Latency", value=f"{ws_ms} ms", inline=True)
                embed.add_field(name="Uptime", value=uptime_str, inline=True)
                embed.add_field(name="Python", value=pyver, inline=True)
                embed.add_field(name="discord.py", value=libver, inline=True)
                embed.add_field(name="CPU / RAM", value=f"{cpu_str} / {ram_str}", inline=True)
                if bot.user and bot.user.display_avatar:
                    embed.set_thumbnail(url=bot.user.display_avatar.url)
                icon = getattr(getattr(ctx.author, "display_avatar", None), "url", None)
                embed.set_footer(text=f"Requested by {ctx.author}", icon_url=icon)

                await ctx.send(embed=embed)
                print("[DEBUG] Stats embed sent")
                return
            except discord.Forbidden as e:
                print("[DEBUG] Forbidden sending embed (likely missing Embed Links). Falling back to text:", repr(e))
            except discord.HTTPException as e:
                print("[DEBUG] HTTPException sending embed. Falling back to text:", repr(e))
            except Exception as e:
                print("[DEBUG] Unexpected error sending embed. Falling back to text:", repr(e))

            # Fallback: PLAIN TEXT (works even without Embed Links)
            lines = [
                "📊 **Bot Statistics**",
                f"Servers: {guilds}",
                f"Members (approx): {members}",
                f"Channels: {total_channels} (T:{text_channels} / V:{voice_channels})",
                f"Commands Loaded: {commands_loaded}",
                f"Latency: {ws_ms} ms",
                f"Uptime: {uptime_str}",
                f"Python: {pyver}",
                f"discord.py: {libver}",
                f"CPU / RAM: {cpu_str} / {ram_str}",
            ]
            await ctx.send("\n".join(lines))
            print("[DEBUG] Stats plaintext sent")

        except discord.Forbidden as e:
            print("[DEBUG] Forbidden sending any message:", repr(e))
            # Last-ditch: try to DM the author to inform about missing perms
            with contextlib.suppress(Exception):
                await ctx.author.send("I don't have permission to send messages in that channel.")
        except Exception as e:
            print("[DEBUG] Stats command crashed:", repr(e))
            # Try to send an error embed; if that fails, send text
            try:
                await ctx.send(embed=mkembed(
                    title="❌ Error in Stats Command",
                    desc=f"`{type(e).__name__}: {e}`",
                    color=COLORS["ERROR"]
                ))
            except Exception:
                with contextlib.suppress(Exception):
                    await ctx.send(f"❌ Error: {type(e).__name__}: {e}")





# ==========================================
#                Prefix command
# ==========================================
    @commands.command(name="prefix")
    async def prefix(self, ctx: commands.Context, *, new: str | None = None):
        """
        Show or change the server prefix.
        Usage:
        ;prefix          → shows current prefix
        ;prefix !        → sets prefix to '!' (requires Manage Server)
        """
        # View current prefix
        if new is None:
            async with AsyncSessionLocal() as session:
                cfg = await _get_guild_cfg(session, str(ctx.guild.id))
                current = cfg.prefix or ";"
            embed = mkembed(
                title="⚙️ Prefix",
                desc=f"Current prefix for **{ctx.guild.name}** is **`{current}`**",
                color=COLORS["INFO"],
            )
            return await ctx.send(embed=embed)

        # Changing prefix: permission check (Manage Server)
        if not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=mkembed(
                title="❌ No Permission",
                desc="You need **Manage Server** to change the prefix.",
                color=COLORS["ERROR"],
            ))

        # Normalize + validate new prefix
        new = new.strip().strip('"').strip("'")
        if not new:
            return await ctx.send(embed=mkembed("❌ Invalid Prefix", "Prefix cannot be empty.", COLORS["ERROR"]))
        if any(ch.isspace() for ch in new):
            return await ctx.send(embed=mkembed("❌ Invalid Prefix", "Prefix cannot contain spaces.", COLORS["ERROR"]))
        if len(new) > 5:
            return await ctx.send(embed=mkembed("❌ Invalid Prefix", "Prefix must be **≤ 5** characters.", COLORS["ERROR"]))
        if new.startswith("<@") and new.endswith(">"):
            return await ctx.send(embed=mkembed("❌ Invalid Prefix", "Mentions cannot be used as a prefix.", COLORS["ERROR"]))

        # Save to DB
        async with AsyncSessionLocal() as session:
            cfg = await _get_guild_cfg(session, str(ctx.guild.id))
            cfg.prefix = new
            session.add(cfg)
            await session.commit()

        # Update in-memory cache (if your bot uses one)
        if not hasattr(self.bot, "prefix_cache"):
            self.bot.prefix_cache = {}
        self.bot.prefix_cache[str(ctx.guild.id)] = new

        # Success
        embed = mkembed(
            title="✅ Prefix Updated",
            desc=f"Prefix for **{ctx.guild.name}** set to **`{new}`**",
            color=COLORS["SUCCESS"],
        )
        await ctx.send(embed=embed)




# ==========================================
#                Feedback command
# ==========================================
    @commands.command(name="feedback")
    @commands.cooldown(1, 30, BucketType.user)
    async def feedback(self, ctx: commands.Context, *, text: str):
        """Send feedback to the developer."""
        if not text.strip():
            return await ctx.send(embed=mkembed("❌ Feedback", "Please include some text.", COLORS["ERROR"]))

        # Build the report embed
        em = mkembed("📝 New Feedback", color=COLORS["INFO"])
        em.add_field(name="From", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        em.add_field(name="Server", value=f"{ctx.guild.name} (`{ctx.guild.id}`)" if ctx.guild else "DM", inline=False)
        em.add_field(name="Channel", value=ctx.channel.mention if hasattr(ctx.channel, "mention") else str(ctx.channel), inline=False)
        em.add_field(name="Content", value=text[:1024], inline=False)

        delivered = False

        # Try configured channel first
        if FEEDBACK_CHANNEL_ID:
            ch = self.bot.get_channel(FEEDBACK_CHANNEL_ID)
            if ch is None:
                with contextlib.suppress(Exception):
                    ch = await self.bot.fetch_channel(FEEDBACK_CHANNEL_ID)
            if ch:
                with contextlib.suppress(Exception):
                    await ch.send(embed=em)
                    delivered = True

        # Fallback: DM application owner
        if not delivered:
            try:
                app = await self.bot.application_info()
                owner = app.owner
                with contextlib.suppress(Exception):
                    await owner.send(embed=em)
                    delivered = True
            except Exception:
                pass

        # Acknowledge
        if delivered:
            await ctx.send(embed=mkembed("✅ Feedback Sent", "Thanks! Your feedback has been delivered.", COLORS["SUCCESS"]))
        else:
            await ctx.send(embed=mkembed("⚠️ Feedback Not Delivered", "I couldn't reach the maintainer. Please try again later.", COLORS["WARNING"]))



# ==========================================
#                Bug command
# ==========================================
    @commands.command(name="bug")
    @commands.cooldown(1, 30, BucketType.user)
    async def bug(self, ctx: commands.Context, *, text: str):
        """Report a bug to the developer."""
        if not text.strip():
            return await ctx.send(embed=mkembed("❌ Bug Report", "Please describe the bug.", COLORS["ERROR"]))

        em = mkembed("🐞 New Bug Report", color=COLORS["ERROR"])
        em.add_field(name="From", value=f"{ctx.author} (`{ctx.author.id}`)", inline=False)
        em.add_field(name="Server", value=f"{ctx.guild.name} (`{ctx.guild.id}`)" if ctx.guild else "DM", inline=False)
        em.add_field(name="Channel", value=ctx.channel.mention if hasattr(ctx.channel, "mention") else str(ctx.channel), inline=False)
        em.add_field(name="Details", value=text[:1024], inline=False)

        delivered = False

        if BUG_CHANNEL_ID:
            ch = self.bot.get_channel(BUG_CHANNEL_ID)
            if ch is None:
                with contextlib.suppress(Exception):
                    ch = await self.bot.fetch_channel(BUG_CHANNEL_ID)
            if ch:
                with contextlib.suppress(Exception):
                    await ch.send(embed=em)
                    delivered = True

        if not delivered:
            try:
                app = await self.bot.application_info()
                owner = app.owner
                with contextlib.suppress(Exception):
                    await owner.send(embed=em)
                    delivered = True
            except Exception:
                pass

        if delivered:
            await ctx.send(embed=mkembed("✅ Bug Report Sent", "Thanks! Your report has been delivered to the maintainer.", COLORS["SUCCESS"]))
        else:
            await ctx.send(embed=mkembed("⚠️ Bug Report Not Delivered", "I couldn't reach the maintainer. Please try again later.", COLORS["WARNING"]))




# ==========================================
#                Help command
# ==========================================
    @commands.command(name="help")
    async def help_cmd(self, ctx: commands.Context, *, query: str | None = None):
        """Show global help, category help, or command help (from JSON)."""
        idx = self.help_index
        bot = self.bot
        pfx = self._prefix(ctx)

        # format usage lines replacing {p} with current prefix
        def fmt_usage(usages: list[str] | None) -> str:
            if not usages:
                return f"`{pfx}help {query or ''}`".strip()
            return "\n".join(f"`{u.replace('{p}', pfx)}`" for u in usages)

        if not query:
            # Global: list categories from JSON if present, else from loaded cogs
            lines = []
            cats = idx.get("categories", {})
            if cats:
                for name, meta in cats.items():
                    cmd_list = ", ".join(meta.get("commands", []))
                    desc = meta.get("desc", "")
                    lines.append(f"**{name}** — {desc}\n{cmd_list}\n")
            else:
                # fallback to loaded cogs
                for cog_name, cog in sorted(bot.cogs.items(), key=lambda kv: kv[0].lower()):
                    cmds = [c for c in cog.get_commands() if not c.hidden]
                    if not cmds: continue
                    cmd_names = ", ".join(sorted(c.name for c in cmds))
                    lines.append(f"**{cog_name}**\n{cmd_names}\n")

            desc = (
                f"Use **`{pfx}help <category>`** or **`{pfx}help <command>`** for details.\n\n" +
                ("\n".join(lines) if lines else "_No commands loaded._")
            )
            return await ctx.send(embed=mkembed("📖 Help", desc, COLORS["INFO"]))

        # Category help (match JSON category first)
        cats = idx.get("categories", {})
        cat_meta = next((meta for name, meta in cats.items() if name.lower() == query.lower()), None)
        if cat_meta:
            em = mkembed(f"📂 {query.title()}", color=COLORS["INFO"])
            em.description = cat_meta.get("desc", "")
            for cmd_name in cat_meta.get("commands", []):
                cmd = bot.get_command(cmd_name)
                if not cmd or cmd.hidden: 
                    continue
                brief = idx.get("commands", {}).get(cmd_name, {}).get("brief") or (cmd.help or "").strip().splitlines()[0] if cmd.help else ""
                em.add_field(name=f"`{pfx}{cmd.qualified_name}`", value=brief or "—", inline=False)
            return await ctx.send(embed=em)

        # Command help
        cmd = bot.get_command(query)
        if cmd:
            meta = idx.get("commands", {}).get(cmd.qualified_name) or idx.get("commands", {}).get(cmd.name, {})
            brief = meta.get("brief") or (cmd.help or "").strip().splitlines()[0] if cmd.help else "No description."
            usage = fmt_usage(meta.get("usage"))
            aliases = ", ".join(cmd.aliases) if cmd.aliases else "None"
            em = mkembed(f"🧩 {cmd.qualified_name}", color=COLORS["INFO"])
            em.description = brief
            em.add_field(name="Usage", value=usage, inline=False)
            em.add_field(name="Aliases", value=aliases, inline=False)
            em.add_field(name="Category", value=cmd.cog_name or "Uncategorized", inline=True)
            em.add_field(name="Hidden", value=str(cmd.hidden), inline=True)
            return await ctx.send(embed=em)

        # Not found
        return await ctx.send(
            embed=mkembed(
                "❓ Not Found",
                f"I couldn't find a category or command named **`{query}`**.",
                COLORS["WARNING"],
            )
        )



# ==========================================
#               Help Refresh
# ==========================================
    @commands.command(name="helprefresh")
    @commands.has_permissions(manage_guild=True)
    async def helprefresh(self, ctx: commands.Context):
        """Reload help descriptions from JSON."""
        self.help_index = self._load_help_index()
        await ctx.send(embed=mkembed("🔄 Help Reloaded", f"Reloaded from `{HELP_JSON_PATH}`.", COLORS["SUCCESS"]))




# ====================END===================
async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
