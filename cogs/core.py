from __future__ import annotations
import json, os, contextlib, platform, importlib.util
from datetime import datetime, timezone, timedelta
from typing import Optional
import discord
from discord.ext import commands
from sqlalchemy import select
from db.engine import AsyncSessionLocal
from db.models import GuildConfig
from discord.ext.commands import BucketType

try:
    import psutil
except ImportError:
    psutil = None

BOT_VERSION = "1.0.0"
DEV_NAME = "<@555982190639579144>, <@1128018348119183410>"

COLORS = {
    "INFO": discord.Color.blurple(),
    "SUCCESS": discord.Color.green(),
    "WARNING": discord.Color.gold(),
    "ERROR": discord.Color.red(),
}

HELP_JSON_PATH = os.path.join(os.getcwd(), "help_descriptions.json")

# ──────────────────────────────────────────────
# Utility functions
# ──────────────────────────────────────────────
def mkembed(title, desc="", color=None):
    return discord.Embed(
        title=title,
        description=desc,
        color=color or COLORS["INFO"],
        timestamp=datetime.now(timezone.utc)
    )

def humanize_tdelta(td: timedelta):
    secs = int(td.total_seconds())
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


# ──────────────────────────────────────────────
# Core Cog
# ──────────────────────────────────────────────
class Core(commands.Cog):
    """Core commands: ping, uptime, stats, help, etc."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.help_index = self._load_help_index()
        self._watch_cogs()
        if not hasattr(self.bot, "boot_time"):
            self.bot.boot_time = datetime.now(timezone.utc)

    # ─────────── HELP SYSTEM ───────────
    def _load_help_index(self):
        try:
            with open(HELP_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("help_descriptions.json invalid format")
        except FileNotFoundError:
            print(f"[HELP] ⚠ File not found: {HELP_JSON_PATH}")
            data = {"categories": {}, "commands": {}}
        except Exception as e:
            print(f"[HELP] ⚠ Failed to load JSON: {e}")
            data = {"categories": {}, "commands": {}}

        cats = len(data.get("categories", {}))
        cmds = len(data.get("commands", {}))
        print(f"[HELP] Synced help_descriptions.json ({cats} categories, {cmds} commands)")
        return data

    def _prefix(self, ctx):
        if hasattr(self.bot, "prefix_cache") and ctx.guild:
            return self.bot.prefix_cache.get(str(ctx.guild.id), ";")
        return ";"

    # Automatically sync on cog load/reload/unload
    def _watch_cogs(self):
        orig_load = self.bot.load_extension
        orig_reload = self.bot.reload_extension
        orig_unload = self.bot.unload_extension

        async def wrapped_load(ext):
            res = await orig_load(ext)
            self.help_index = self._load_help_index()
            return res

        async def wrapped_reload(ext):
            res = await orig_reload(ext)
            self.help_index = self._load_help_index()
            return res

        async def wrapped_unload(ext):
            res = await orig_unload(ext)
            self.help_index = self._load_help_index()
            return res

        self.bot.load_extension = wrapped_load
        self.bot.reload_extension = wrapped_reload
        self.bot.unload_extension = wrapped_unload

    # ─────────── BASIC COMMANDS ───────────
    @commands.command(name="ping")
    async def ping(self, ctx):
        ws_ms = round(self.bot.latency * 1000, 2)
        color = COLORS["SUCCESS"] if ws_ms < 200 else COLORS["WARNING"] if ws_ms < 400 else COLORS["ERROR"]
        await ctx.send(embed=mkembed("🏓 Pong!", f"WebSocket latency: **{ws_ms}ms**", color))

    @commands.command(name="uptime")
    async def uptime(self, ctx):
        boot = getattr(self.bot, "boot_time", datetime.now(timezone.utc))
        delta = datetime.now(timezone.utc) - boot
        pretty = humanize_tdelta(delta)
        await ctx.send(embed=mkembed("⏱ Uptime", f"Online for **{pretty}**", COLORS["INFO"]))

    # ─────────── HELP COMMAND ───────────
    @commands.command(name="help")
    async def help_cmd(self, ctx, *, query: str | None = None):
        idx = self._load_help_index()
        prefix = self._prefix(ctx)
        categories = idx.get("categories", {})
        commands_info = idx.get("commands", {})

        if not query:
            embed = mkembed("📘 Helix Help", f"Use `{prefix}help <category>` or `{prefix}help <command>`.", COLORS["INFO"])
            for cat, meta in categories.items():
                cmds = ", ".join(meta.get("commands", []))
                embed.add_field(name=f"📂 {cat}", value=f"{meta.get('desc', '')}\n**Commands:** {cmds or '_None_'}", inline=False)
            return await ctx.send(embed=embed)

        query = query.lower()

        # Category help
        for cat, meta in categories.items():
            if cat.lower() == query:
                embed = mkembed(f"📂 {cat} Commands", meta.get("desc", ""), COLORS["INFO"])
                for cmd in meta.get("commands", []):
                    brief = commands_info.get(cmd, {}).get("brief", "—")
                    embed.add_field(name=f"{prefix}{cmd}", value=brief, inline=False)
                return await ctx.send(embed=embed)

        # Command help
        cmd_data = commands_info.get(query)
        if not cmd_data:
            return await ctx.send(embed=mkembed("❌ Help", f"No command named `{query}`.", COLORS["ERROR"]))

        desc = cmd_data.get("desc", "No description provided.")
        embed = mkembed(f"🧩 {query}", desc, COLORS["INFO"])
        if usage := cmd_data.get("usage"):
            usage_text = "\n".join(f"`{u.replace('{p}', prefix)}`" for u in usage)
            embed.add_field(name="Usage", value=usage_text, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="helprefresh")
    @commands.has_permissions(manage_guild=True)
    async def helprefresh(self, ctx):
        self.help_index = self._load_help_index()
        await ctx.send(embed=mkembed("🔄 Help Reloaded", "Descriptions reloaded successfully.", COLORS["SUCCESS"]))


# ─────────── Cog setup ───────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
