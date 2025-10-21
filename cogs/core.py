from __future__ import annotations
import json, os, contextlib
import platform, sys, pkgutil
import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Optional
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
        self._ensure_help_index()

    def _ensure_help_index(self) -> dict:
        """Always return a valid help index and keep self.help_index up to date."""
        try:
            with open(HELP_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("help json must be a JSON object")
        except FileNotFoundError:
            print(f"[HELP] File not found: {HELP_JSON_PATH}")
            data = {"categories": {}, "commands": {}}
        except Exception as e:
            print(f"[HELP] Failed to load help json: {e}")
            data = {"categories": {}, "commands": {}}

        # shape guard
        data.setdefault("categories", {})
        data.setdefault("commands", {})
        self.help_index = data
        return self.help_index

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
            f"**{bot.user.name}** — where logic meets evolution.\n"
            f"Version: `{BOT_VERSION}` • Developers: **{DEV_NAME}**\n\n"
            f"**Servers:** {guilds}\n"
            f"**Members (approx):** {members}\n"
            f"**Latency:** {ws_ms} ms\n"
            f"**Uptime:** {uptime}\n"
        )

        embed = mkembed(title="ℹ️ About This Bot", desc=desc, color=COLORS["INFO"])
        if bot.user and bot.user.display_avatar:
            embed.set_thumbnail(url=bot.user.display_avatar.url)

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
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def help_cmd(self, ctx: commands.Context, *, query: str | None = None):
        """
        Classic help embed — shows all data from help_descriptions.json.
        Supports: ;help, ;help <category>, ;help <command>
        """
        idx = self._ensure_help_index()
        prefix = ";"
        if hasattr(self.bot, "prefix_cache") and ctx.guild:
            prefix = self.bot.prefix_cache.get(str(ctx.guild.id), ";")

        categories = idx.get("categories", {})
        commands_info = idx.get("commands", {})

        # ───────────── GLOBAL HELP OVERVIEW ─────────────
        if not query:
            embed = mkembed("📘 Helix Help", "Browse commands by category.\nUse `{p}help <category>` or `{p}help <command>` for details.".replace("{p}", prefix), COLORS["INFO"])
            for cat_name, meta in categories.items():
                cmd_list = ", ".join(meta.get("commands", []))
                desc = meta.get("desc", "")
                embed.add_field(
                    name=f"📂 {cat_name}",
                    value=f"{desc}\n**Commands:** {cmd_list if cmd_list else '_None_'}",
                    inline=False,
                )
            embed.set_footer(text=f"Use {prefix}help <command> for more details.")
            return await ctx.send(embed=embed)

        query = query.strip().lower()

        # ───────────── CATEGORY HELP ─────────────
        for cat_name, meta in categories.items():
            if cat_name.lower() == query:
                embed = mkembed(f"📂 {cat_name} Commands", meta.get("desc", "No description."), COLORS["INFO"])
                for cmd_name in meta.get("commands", []):
                    cmd_data = commands_info.get(cmd_name, {})
                    brief = cmd_data.get("brief", "—")
                    embed.add_field(name=f"{prefix}{cmd_name}", value=brief, inline=False)
                embed.set_footer(text=f"Use {prefix}help <command> for command-specific help.")
                return await ctx.send(embed=embed)

        # ───────────── COMMAND HELP ─────────────
        cmd_data = commands_info.get(query)
        if not cmd_data:
            # try to match by partial command name
            for name, data in commands_info.items():
                if query == name.lower():
                    cmd_data = data
                    break

        if not cmd_data:
            return await ctx.send(embed=mkembed("❌ Help", f"Couldn't find a category or command named `{query}`.", COLORS["ERROR"]))

        title = f"🧩 {query}"
        desc = cmd_data.get("desc", "No description available.")
        brief = cmd_data.get("brief", None)

        embed = mkembed(title, desc, COLORS["INFO"])
        if brief:
            embed.add_field(name="Summary", value=brief, inline=False)

        # Usage examples
        usage_list = cmd_data.get("usage", [])
        if usage_list:
            usage_text = "\n".join(f"`{u.replace('{p}', prefix)}`" for u in usage_list)
            embed.add_field(name="Usage", value=usage_text, inline=False)

        # Subcommands / variants
        subcommands = cmd_data.get("subcommands", [])
        if subcommands:
            for sub in subcommands:
                sub_name = sub.get("name", "Unnamed")
                sub_brief = sub.get("brief", "")
                sub_usage = sub.get("usage", [])
                sub_text = "\n".join(f"`{u.replace('{p}', prefix)}`" for u in sub_usage)
                embed.add_field(
                    name=f"• {sub_name}",
                    value=f"{sub_brief}\n{sub_text}",
                    inline=False
                )

        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=getattr(ctx.author.display_avatar, 'url', None))
        await ctx.send(embed=embed)





# ==========================================
#               Help Refresh
# ==========================================
    @commands.command(name="helprefresh")
    @commands.has_permissions(manage_guild=True)
    async def helprefresh(self, ctx: commands.Context):
        self._ensure_help_index()
        """Reload help descriptions from JSON."""
        self.help_index = self._load_help_index()
        await ctx.send(embed=mkembed("🔄 Help Reloaded", f"!", COLORS["SUCCESS"]))



# =========================================
#                Help UI
# ========================================
    @commands.command(name="helpui")
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def helpui(self, ctx: commands.Context):
        idx = self._ensure_help_index()
        """
        Open an interactive Help UI with dropdowns for categories, commands, and usages.
        Example: {p}helpui
        """
        index = getattr(self, "help_index", None)
        if not index:
            # safety: try to load if not set for some reason
            index = self._load_help_index()
            self.help_index = index

        view = HelpView(self, ctx, idx)
        embed = view.build_embed()
        await ctx.send(embed=embed, view=view)

# =====================================================================
# =====================    HELP UI CLASSES    =========================
# =====================================================================
class HelpState:
    def __init__(self):
        self.category: str | None = None
        self.command: str | None = None
        self.usage_index: int | None = None


class HelpView(discord.ui.View):
    def __init__(self, core_cog: "Core", ctx: commands.Context, index: dict, *, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.core = core_cog
        self.ctx = ctx
        self.index = index
        self.state = HelpState()
        self.message: Optional[discord.Message] = None  # set after send()

        # Dropdowns
        self.category_select = CategorySelect(self)
        self.command_select = CommandSelect(self)
        self.usage_select = UsageSelect(self)

        self.add_item(self.category_select)
        self.add_item(self.command_select)
        self.add_item(self.usage_select)

    # ---- REQUIRED HELPERS (these were missing) ----
    def category_list(self) -> list[str]:
        return list(self.index.get("categories", {}).keys())

    def commands_in_category(self, cat: str | None) -> list[str]:
        if not cat:
            return []
        meta = self.index.get("categories", {}).get(cat, {})
        return meta.get("commands", [])

    def command_meta(self, cmd: str) -> dict:
        return (self.index.get("commands", {}) or {}).get(cmd, {})

    # ---- Misc helpers ----
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the command invoker can use this help menu.", ephemeral=True
            )
            return False
        return True

    def _prefix(self) -> str:
        if hasattr(self.core.bot, "prefix_cache") and self.ctx.guild:
            return self.core.bot.prefix_cache.get(str(self.ctx.guild.id), ";")
        return ";"

    def build_embed(self) -> discord.Embed:
        p = self._prefix()
        st = self.state

        # Overview
        if st.category is None:
            lines = []
            for cat in self.category_list():
                meta = self.index["categories"].get(cat, {})
                desc = meta.get("desc", "")
                cmds = ", ".join(meta.get("commands", []))
                lines.append(f"**{cat}** — {desc}\n{cmds}\n")
            desc = (
                "Use the dropdowns below to browse categories, commands, and examples.\n\n"
                + ("\n".join(lines) if lines else "_No categories found._")
            )
            return mkembed("📖 Help — Overview", desc, COLORS["INFO"])

        # Category view
        if st.command is None:
            meta = self.index["categories"].get(st.category, {})
            desc = meta.get("desc", "")
            em = mkembed(f"📂 {st.category}", desc, COLORS["INFO"])
            for cmd_name in self.commands_in_category(st.category):
                cmeta = self.command_meta(cmd_name)
                brief = cmeta.get("brief") or "—"
                em.add_field(name=f"`{p}{cmd_name}`", value=brief, inline=False)
            return em

        # Command view
        cmeta = self.command_meta(st.command)
        brief = cmeta.get("brief") or "No description."
        usage_list = cmeta.get("usage") or [f"{p}{st.command}"]
        em = mkembed(f"🧩 {st.command}", brief, COLORS["INFO"])
        em.add_field(
            name="Usage",
            value="\n".join(f"`{u.replace('{p}', p)}`" for u in usage_list),
            inline=False,
        )
        if st.usage_index is not None and 0 <= st.usage_index < len(usage_list):
            chosen = usage_list[st.usage_index].replace("{p}", p)
            em.add_field(name="Selected Example", value=f"`{chosen}`", inline=False)
        em.set_footer(text=f"Category: {st.category or 'Unknown'}")
        return em

    async def refresh(self, interaction: discord.Interaction):
        self.command_select.rebuild_options()
        self.usage_select.rebuild_options()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                child.disabled = True
        if self.message:
            with contextlib.suppress(Exception):
                await self.message.edit(view=self)


class CategorySelect(discord.ui.Select):
    def __init__(self, view: HelpView):
        self.helpview = view
        options = [
            discord.SelectOption(
                label=cat,
                description=(view.index["categories"].get(cat, {}).get("desc", "")[:90] or None),
            )
            for cat in view.category_list()
        ]
        super().__init__(placeholder="Choose a category…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        self.helpview.state.category = chosen
        self.helpview.state.command = None
        self.helpview.state.usage_index = None
        await self.helpview.refresh(interaction)


class CommandSelect(discord.ui.Select):
    def __init__(self, view: HelpView):
        self.helpview = view
        super().__init__(placeholder="Choose a command…", min_values=1, max_values=1, options=[], disabled=True)
        self.rebuild_options()

    def rebuild_options(self):
        cat = self.helpview.state.category
        cmds = self.helpview.commands_in_category(cat)
        opts = []
        for name in cmds:
            brief = self.helpview.command_meta(name).get("brief") or ""
            opts.append(discord.SelectOption(label=name, description=(brief[:90] or None)))
        self.options = opts
        self.disabled = not bool(opts)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        self.helpview.state.command = chosen
        self.helpview.state.usage_index = None
        await self.helpview.refresh(interaction)


class UsageSelect(discord.ui.Select):
    def __init__(self, view: HelpView):
        self.helpview = view
        super().__init__(placeholder="Choose an example usage…", min_values=1, max_values=1, options=[], disabled=True)
        self.rebuild_options()

    def rebuild_options(self):
        cmd = self.helpview.state.command
        if not cmd:
            self.options = []
            self.disabled = True
            return
        p = self.helpview._prefix()
        usage = self.helpview.command_meta(cmd).get("usage") or [f"{p}{cmd}"]
        opts = []
        for i, u in enumerate(usage):
            label = u.replace("{p}", p)
            opts.append(discord.SelectOption(label=label[:100], value=str(i)))
        self.options = opts
        self.disabled = not bool(opts)

    async def callback(self, interaction: discord.Interaction):
        try:
            idx = int(self.values[0])
        except Exception:
            idx = 0
        self.helpview.state.usage_index = idx
        await self.helpview.refresh(interaction)




# ====================END===================
async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
