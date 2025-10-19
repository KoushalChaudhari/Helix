import aiohttp
from discord.ext import commands
from config import TENOR_KEY

TENOR_SEARCH = "https://tenor.googleapis.com/v2/search"

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def gif(self, ctx: commands.Context, *, query: str | None = None):
        if not TENOR_KEY:
            return await ctx.reply("Tenor API key not configured.")
        if not query:
            return await ctx.reply("Usage: `;gif <query>`")

        params = {
            "q": query,
            "key": TENOR_KEY,
            "client_key": "jackbot",
            "limit": 1,
            "media_filter": "minimal",
            "random": "true"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(TENOR_SEARCH, params=params) as resp:
                if resp.status != 200:
                    return await ctx.reply("Tenor request failed.")
                data = await resp.json()
        try:
            url = data["results"][0]["media_formats"]["gif"]["url"]
        except Exception:
            return await ctx.reply("No GIF found.")
        await ctx.reply(url)

async def setup(bot):
    await bot.add_cog(Fun(bot))
