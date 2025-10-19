import discord
from discord.ext import commands

class Core(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command()
    async def ping(self, ctx: commands.Context):
        m = await ctx.send("Pong!")
        await m.edit(content=f"Pong! {m.created_at - ctx.message.created_at}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Core(bot))
