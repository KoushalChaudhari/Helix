# bot.py
from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

import discord
from discord.ext import commands
from sqlalchemy import select

# DB imports (adjust if your paths differ)
from db.engine import AsyncSessionLocal, init_db
from db.models import Base, GuildConfig

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
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("joat")

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
    log.info("🛜 Connected to %d guild(s).", len(bot.guilds))
    try:
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=";help")
        )
    except Exception:
        pass

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # Graceful, minimal error reporting in chat; details go to log
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use that.")
        return
    if isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ I’m missing required permissions to do that here.")
        return
    if isinstance(error, commands.CommandNotFound):
        return  # silently ignore unknown commands (common with prefix changes)
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: `{error.param.name}`")
        return

    log.exception("Unhandled command error in %s: %s", ctx.command, error)
    try:
        await ctx.send(f"⚠️ An unexpected error occurred: `{type(error).__name__}`")
    except Exception:
        pass

# ---------------------------------------------------------------------
#  Startup
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

if __name__ == "__main__":
    asyncio.run(main())
