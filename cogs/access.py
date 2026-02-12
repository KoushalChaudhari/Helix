# cogs/access.py
import json
import os
from typing import Dict, Set
import discord
from discord.ext import commands
import io
import re
import asyncio
import contextlib
from typing import Optional, Tuple, List

ALLOWLIST_FILE = "allowlist.json"


class AccessControl(commands.Cog):
    """Global role-based allowlist for all commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # { guild_id: {role_id, role_id, ...} }
        self.guild_allowed_roles: Dict[int, Set[int]] = {}
        self._load_allowlist()

    # ----------------- persistence -----------------
    def _load_allowlist(self):
        if not os.path.exists(ALLOWLIST_FILE):
            self.guild_allowed_roles = {}
            return
        try:
            with open(ALLOWLIST_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # convert role id lists back to sets of ints
            self.guild_allowed_roles = {
                int(gid): {int(rid) for rid in roles}
                for gid, roles in raw.items()
            }
        except Exception:
            # if file is corrupted, start fresh (you can log if you want)
            self.guild_allowed_roles = {}

    def _save_allowlist(self):
        data = {
            str(gid): [int(rid) for rid in roles]
            for gid, roles in self.guild_allowed_roles.items()
        }
        with open(ALLOWLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ----------------- global check -----------------
    async def global_allow_check(self, ctx: commands.Context) -> bool:
        """Global check: only admins or allowlisted roles (plus owner) can use commands."""

        # ignore non-commands
        if ctx.command is None:
            return False

        # always allow bot owner
        if await ctx.bot.is_owner(ctx.author):
            return True

        # DMs: only owner can use commands (you can relax this if you want)
        if ctx.guild is None:
            return False

        member: discord.Member = ctx.author  # in guild context this is always Member

        # default: admin roles can use everything
        if member.guild_permissions.administrator:
            return True

        # check guild-specific allowed roles
        allowed_roles = self.guild_allowed_roles.get(ctx.guild.id, set())
        if any(r.id in allowed_roles for r in member.roles):
            return True

        # not allowed
        raise commands.CheckFailure("You are not allowed to use this bot here.")

    async def cog_load(self):
        # register global check when cog is loaded
        self.bot.add_check(self.global_allow_check)

    async def cog_unload(self):
        # remove it when cog unloads
        self.bot.remove_check(self.global_allow_check)

    # ----------------- commands -----------------
    @commands.command(name="allowrole", aliases=["togglerole"])
    @commands.has_permissions(administrator=True)
    async def allowrole(self, ctx: commands.Context, role: discord.Role):
        """
        Toggle a role's access to all bot commands in this server.
        Usage:
          ;allowrole @SomeRole
        Default behavior (even without allowlist):
          - Admins can always use commands.
        """
        if ctx.guild is None:
            return await ctx.reply("❌ This command can only be used in a server.")

        guild_id = ctx.guild.id
        if guild_id not in self.guild_allowed_roles:
            self.guild_allowed_roles[guild_id] = set()

        role_set = self.guild_allowed_roles[guild_id]

        if role.id in role_set:
            role_set.remove(role.id)
            self._save_allowlist()
            return await ctx.reply(
                f"🔓 Role {role.mention} **removed** from allowlist. "
                "Members with only this role (and no admin perms) will lose access."
            )
        else:
            role_set.add(role.id)
            self._save_allowlist()
            return await ctx.reply(
                f"🔐 Role {role.mention} **added** to allowlist. "
                "Members with this role can now use the bot (even if non-admin)."
            )

    @allowrole.error
    async def allowrole_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("🚫 You need **Administrator** permission to manage bot access.")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("❌ Invalid role. Mention a role or provide a valid role ID.")
        else:
            # fall through so your global error handler (if any) can handle it
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(AccessControl(bot))
