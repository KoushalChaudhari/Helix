# bot.py
import asyncio
import discord
from discord.ext import commands

from config import DISCORD_TOKEN, PREFIX
from db.engine import init_db
from db.models import Base

# ---- Intents (require enabling in Developer Portal: Message Content + Server Members) ----
intents = discord.Intents.default()
intents.message_content = True   # prefix commands like ;gif, ;ping
intents.members = True           # role/member utilities later

# ---- Bot ----
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})") # type: ignore
    try:
        await bot.change_presence(activity=discord.Game(name=f"{PREFIX}help"))
    except Exception:
        pass

async def load_extensions():
    """Load your cogs here."""
    extensions = (
        "cogs.core",    
        "cogs.fun",     
        "cogs.dbtest",  
        "cogs.mod",
    )
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            print(f"[Cogs] Loaded: {ext}")
        except Exception as e:
            print(f"[Cogs] Failed to load {ext}: {e}")

async def main():
    # 1) Ensure DB is reachable and tables exist
    await init_db(Base.metadata)
    print("[DB] Connected to Postgres and ensured tables.")

    # 2) Load command modules
    await load_extensions()

    # 3) Start the bot
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())