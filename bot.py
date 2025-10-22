# bot.py
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from db.engine import init_db, AsyncSessionLocal
from db.models import GuildConfig
from sqlalchemy import select

# ─────────── Load environment variables ───────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
APP_ID = os.getenv("APP_ID")

# ─────────── Intents & Basic Setup ───────────
intents = discord.Intents.all()
intents.message_content = True
intents.members = True
intents.presences = False

# ─────────── Bot Configuration ───────────
class HelixBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=self.get_prefix,
            intents=intents,
            application_id=APP_ID,
            help_command=None,  # using custom help
        )
        self.boot_time = discord.utils.utcnow()
        self.prefix_cache = {}
        self.initial_extensions = [
            "cogs.core",
            "cogs.mod",
            "cogs.logs",
            "cogs.userinfo",
        ]

    async def setup_hook(self):
        # Initialize database
        await init_db()

        # Load all extensions safely
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                print(f"[COG] ✅ Loaded {ext}")
            except Exception as e:
                print(f"[COG] ❌ Failed to load {ext}: {e}")

        print(f"[BOT] ✅ All cogs loaded successfully.")

    async def get_prefix(self, message):
        if not message.guild:
            return ";"
        gid = str(message.guild.id)
        if gid in self.prefix_cache:
            return self.prefix_cache[gid]

        # Load prefix from DB
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == gid))
            cfg = res.scalar_one_or_none()
            if cfg and cfg.prefix:
                prefix = cfg.prefix
            else:
                prefix = ";"
            self.prefix_cache[gid] = prefix
            return prefix

    async def on_ready(self):
        print(f"\n{'='*40}")
        print(f"💠 Logged in as: {self.user} (ID: {self.user.id})")
        print(f"🌐 Connected to {len(self.guilds)} guild(s)")
        print(f"💫 Prefix cache size: {len(self.prefix_cache)}")
        print(f"✅ Helix is online and ready!")
        print(f"{'='*40}\n")

        # Set bot presence
        await self.change_presence(
            activity=discord.Game(name="with evolution | ;help"),
            status=discord.Status.online
        )

    async def on_command_error(self, ctx, error):
        """Cleaner user-friendly error messages."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=discord.Embed(
                title="❌ Permission Denied",
                description="You don’t have permission to use this command.",
                color=discord.Color.red()
            ))
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send(embed=discord.Embed(
                title="🤖 Missing Bot Permissions",
                description="I don’t have enough permissions to do that.",
                color=discord.Color.red()
            ))
        elif isinstance(error, commands.CommandNotFound):
            return  # ignore unknown commands silently
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=discord.Embed(
                title="⚠️ Missing Argument",
                description=f"Usage: `{ctx.prefix}{ctx.command.name} {ctx.command.signature}`",
                color=discord.Color.gold()
            ))
        else:
            await ctx.send(embed=discord.Embed(
                title="❌ Error",
                description=f"An unexpected error occurred: `{type(error).__name__}`\n{error}",
                color=discord.Color.red()
            ))
            raise error  # still print full traceback for devs


# ─────────── Main Entrypoint ───────────
async def main():
    async with HelixBot() as bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Bot stopped manually.")
