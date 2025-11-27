# bot.py
from __future__ import annotations

import asyncio
import logging
import os
import traceback

from typing import Dict

import discord
from discord.ext import commands
from sqlalchemy import select

# DB imports (adjust if your paths differ)
from db.engine import AsyncSessionLocal, init_db
from db.models import Base, GuildConfig

from cogs.core import mkembed, COLORS



intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True


# ---------------------------------------------------------------------
# Config / Secrets
# ---------------------------------------------------------------------
# If you use a config.py that loads .env, import from there:
try:
    from config import DISCORD_TOKEN  # Prefer using your existing config loader
except Exception:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Put it in .env or config.py")

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger("🧬 Helix")

# ---------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True      # needed for prefix commands
intents.members = True              # moderation & member cache
intents.guilds = True

# ---------------------------------------------------------------------
# Dynamic Prefix (per-guild)
# ---------------------------------------------------------------------
DEFAULT_PREFIX = ";"
prefix_cache: Dict[str, str] = {}  # guild_id -> prefix

async def load_prefixes() -> None:
    """Warm in-memory prefix cache from DB."""
    prefix_cache.clear()
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(GuildConfig.guild_id, GuildConfig.prefix))
        for gid, pref in res.all():
            prefix_cache[str(gid)] = pref or DEFAULT_PREFIX
    log.info("⚡ Prefix cache warmed for %d guild(s).", len(prefix_cache))

def get_prefix(bot: commands.Bot, message: discord.Message):
    """Dynamic per-guild prefix; also supports @mention as prefix."""
    if not message.guild:
        return commands.when_mentioned_or(DEFAULT_PREFIX)(bot, message)
    pref = prefix_cache.get(str(message.guild.id), DEFAULT_PREFIX)
    return commands.when_mentioned_or(pref)(bot, message)

# ---------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------
bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)
bot.prefix_cache = prefix_cache  # expose to cogs (e.g., ;prefix command updates this)
bot.boot_time = None             # set on_ready

# ---------------------------------------------------------------------
# Cog Loader
# ---------------------------------------------------------------------
EXTENSIONS = [
    "cogs.core",
    "cogs.mod",
    "cogs.fun",
    "cogs.userinfo",
    "cogs.utility",
    "cogs.secret",
    "cogs.access",
]

async def load_extensions():
    for ext in EXTENSIONS:
        try:
            await bot.load_extension(ext)
            log.info("⚙️ Loaded extension: %s", ext)
        except Exception as e:
            log.exception("Failed to load extension %s: %s", ext, e)

# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------
@bot.event
async def on_ready():
    if bot.boot_time is None:
        from datetime import datetime, timezone
        bot.boot_time = datetime.now(timezone.utc)

    # Application id can be useful for invite links in cogs
    try:
        app = await bot.application_info()
        bot.application_id = app.id
    except Exception:
        pass

    log.info("✅ Logged in as %s (ID: %s)", bot.user, getattr(bot.user, "id", "N/A"))
    try:
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.playing, name="Evolution")
        )
    except Exception:
        pass

@bot.event
async def on_command_error(ctx: commands.Context, error):
    """Global error handler for all commands."""
    # If a command-specific handler exists, let it handle things.
    if getattr(ctx, "command", None) and hasattr(ctx.command, "on_error"):
        return

    # Unwrap CommandInvokeError to the original
    error = getattr(error, "original", error)

    # Quietly ignore these common/benign errors
    IGNORED = (
        commands.CommandNotFound,
        commands.CheckFailure,           # includes MissingPermissions for the caller
        commands.CommandOnCooldown,
        commands.NotOwner,
    )
    if isinstance(error, IGNORED):
        return

    # User-facing, structured errors
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.reply(
            embed=mkembed(
                "❌ Missing Argument",
                f"You're missing a required argument: **{error.param.name}**",
                COLORS["ERROR"],
            ),
            delete_after=8,
        )

    if isinstance(error, commands.BadArgument):
        return await ctx.reply(
            embed=mkembed(
                "⚠️ Invalid Input",
                f"Invalid argument or format.\n`{error}`",
                COLORS["WARNING"],
            ),
            delete_after=8,
        )

    if isinstance(error, commands.MissingPermissions):
        needed = ", ".join(error.missing_permissions)
        return await ctx.reply(
            embed=mkembed(
                "🚫 Missing Permissions",
                f"You need: `{needed}`",
                COLORS["ERROR"],
            ),
            delete_after=8,
        )

    if isinstance(error, commands.BotMissingPermissions):
        needed = ", ".join(error.missing_permissions)
        return await ctx.reply(
            embed=mkembed(
                "🤖 Missing Bot Permissions",
                f"I need: `{needed}` to run that command.",
                COLORS["ERROR"],
            ),
            delete_after=8,
        )

    # Fallback: unexpected error — log full traceback, show short notice
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    print(f"[Error] {error}\n{tb}")

    try:
        await ctx.reply(
            embed=mkembed(
                "⚠️ Unexpected Error",
                f"`{type(error).__name__}: {error}`",
                COLORS["ERROR"],
            ),
            delete_after=10,
        )
    except discord.Forbidden:
        pass

# ---------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------
async def main():
    # 1) Ensure DB is ready
    await init_db(Base.metadata)
    log.info("📦 DB initialized / migrations applied.")

    # 2) Warm prefix cache before loading cogs
    await load_prefixes()

    # 3) Load cogs
    await load_extensions()

    # 4) Start the bot
    await bot.start(DISCORD_TOKEN)

# ---------------------------------------------------------------------
# owners
# ---------------------------------------------------------------------
OWNERS = os.getenv("OWNER_IDS", "").split(",")

if __name__ == "__main__":
    asyncio.run(main())
