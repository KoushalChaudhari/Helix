# cogs/clone.py
import io
import re
import asyncio
import contextlib
from typing import Optional, Tuple, List
import discord
from discord.ext import commands

# Example: a local test file is available at /mnt/data/bot.py (useful for dev testing attachments)
# /mnt/data/bot.py

class Clone(commands.Cog):
    """Owner-only cross-server message cloning tool."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.delay_between = 5.0  # seconds between each sent message

    # ------------------ main command ------------------
    @commands.is_owner()
    @commands.command(name="clone")
    async def clone(self, ctx: commands.Context, n: int, src_ref: str, tgt_ref: str):
        """
        Usage: ;clone <n> <source_channel_or_link_or_message_link> <target_channel_or_link>
        Example: ;clone 25 https://discord.com/channels/111/222 333444555666777888
        - n is mandatory
        - owner only
        """
        # Validate n
        if n <= 0:
            return await ctx.reply("❌ `n` must be a positive integer.")

        await ctx.reply("⏳ Starting clone job. Summary will be DM'd to you when finished.", delete_after=6)

        # 1) Resolve channels
        src_channel = await self._resolve_channel_or_channel_from_message_ref(ctx, src_ref)
        tgt_channel = await self._resolve_channel_ref(ctx, tgt_ref)

        if not src_channel or not tgt_channel:
            return await ctx.reply("❌ Could not resolve source or target channel. Use channel/message links, IDs, or #mentions.")
        
        # Prevent cloning the same channel the command is invoked from
        if src_channel.id == ctx.channel.id:
            return await ctx.reply(
                "⚠️ You cannot clone messages **from** the same channel you're running the command in.\n"
                "Please run the command from a *different* channel."
            )


        # Reject DM channels
        if src_channel.type == discord.ChannelType.private or tgt_channel.type == discord.ChannelType.private:
            return await ctx.reply("❌ DM channels are not supported for cloning.")

        # 2) Permissions check (per guild, not reusing src member for target)
        bot_member_src = src_channel.guild.get_member(self.bot.user.id) if src_channel.guild else None
        bot_member_tgt = tgt_channel.guild.get_member(self.bot.user.id) if tgt_channel.guild else None

        if isinstance(src_channel, discord.abc.GuildChannel) and bot_member_src:
            src_perms = src_channel.permissions_for(bot_member_src)
        else:
            # fallback (shouldn't be hit for normal text channels)
            src_perms = src_channel.permissions_for(self.bot.user)

        if isinstance(tgt_channel, discord.abc.GuildChannel) and bot_member_tgt:
            tgt_perms = tgt_channel.permissions_for(bot_member_tgt)
        else:
            tgt_perms = tgt_channel.permissions_for(self.bot.user)

        missing = []
        if not src_perms.read_message_history:
            missing.append("source:read_message_history")
        if not tgt_perms.send_messages:
            missing.append("target:send_messages")
        if not tgt_perms.attach_files:
            missing.append("target:attach_files")
        if not tgt_perms.embed_links:
            missing.append("target:embed_links")

        if missing:
            return await ctx.reply(
                f"❌ Missing permissions: {', '.join(missing)}. "
                "Bot should ideally have Administrator."
            )

        # 3) Fetch last n messages from source (newest->oldest), then reverse to oldest->newest
        msgs = [m async for m in src_channel.history(limit=n)]
        msgs.reverse()

        copied = 0
        skipped = 0
        errors: List[str] = []

        # 4) Clone loop
        for m in msgs:
            # skip system and webhook messages
            if m.type != discord.MessageType.default or getattr(m, "webhook_id", None):
                skipped += 1
                continue

            # build content: prepend author
            author_display = m.author.display_name if getattr(m.author, "display_name", None) else str(m.author)
            sanitized = self._sanitize_content(m.content or "", m.guild)
            content = f"**{author_display}:** {sanitized}" if sanitized else f"**{author_display}**"

            # prepare files (download attachments)
            files = []
            for att in m.attachments:
                try:
                    # local test path support (dev only)
                    if str(att.url).startswith("/mnt/data/"):
                        fobj = open(att.url, "rb")
                        files.append(discord.File(fobj, filename=att.filename))
                    else:
                        data = await att.read()
                        files.append(discord.File(io.BytesIO(data), filename=att.filename))
                except Exception as e:
                    # if we can't re-upload the file, at least keep a link to it
                    errors.append(f"attachment error (msg {m.id}, {att.filename}): {e}")
                    # append a line to the content so the file isn't "lost"
                    link_line = f"\n⚠️ Failed to clone attachment: [{att.filename}]({att.url})"
                    content = (content or "") + link_line
                    # and continue without adding to files

                    # skip this attachment and continue

            # copy embeds (best-effort)
            new_embeds = []
            for emb in m.embeds:
                try:
                    new_embeds.append(discord.Embed.from_dict(emb.to_dict()))
                except Exception:
                    # skip embed if cannot rebuild
                    pass

            # send with rate-limiting and error handling
            try:
                chunks = self._chunk_content(content, 2000)

                for idx, chunk in enumerate(chunks):
                    await tgt_channel.send(
                        content=chunk or None,
                        embeds=new_embeds or None if idx == 0 else None,
                        files=files or None if idx == 0 else None
                    )

                copied += 1

            except Exception as e:
                skipped += 1
                errors.append(f"send error (msg {m.id}): {e}")

            finally:
                # close any opened file objects for local files
                for f in files:
                    with contextlib.suppress(Exception):
                        if hasattr(f, "fp") and not f.fp.closed:
                            f.fp.close()

            # wait between messages to avoid spam/rate-limits
            await asyncio.sleep(self.delay_between)

        # 5) send DM summary to invoker and short ack
        summary_lines = [
            f"✅ Clone finished",
            f"Requested: {n}",
            f"Cloned: {copied}",
            f"Skipped: {skipped}",
            f"Errors: {len(errors)}"
        ]
        if errors:
            summary_lines.append("")
            summary_lines.extend(errors[:12])
            if len(errors) > 12:
                summary_lines.append(f"...and {len(errors)-12} more errors.")

        # DM the invoker (owner)
        with contextlib.suppress(Exception):
            await ctx.author.send("\n".join(summary_lines))

        # short ack in channel
        await ctx.reply("✅ Clone job finished — summary DM'd to you.", delete_after=10)



#  ------------------ single message clone command ------------------

    @commands.is_owner()
    @commands.command(name="clonemsg")
    async def clonemsg(self, ctx: commands.Context, msg_ref: str, target_ref: str):
        """
        Clone a single message by message ID or message link:
        ;clonemsg <message_id|message_link> <channel|id|#mention>
        """
        # Resolve target channel
        tgt_channel = await self._resolve_channel_ref(ctx, target_ref)
        if not tgt_channel:
            return await ctx.reply("❌ Could not resolve target channel.")

        if tgt_channel.type == discord.ChannelType.private:
            return await ctx.reply("❌ DM channels are not supported.")

        # Permissions for target channel
        bot_member_tgt = tgt_channel.guild.get_member(self.bot.user.id)
        tgt_perms = tgt_channel.permissions_for(bot_member_tgt)

        missing = []
        if not tgt_perms.send_messages:
            missing.append("target:send_messages")
        if not tgt_perms.attach_files:
            missing.append("target:attach_files")
        if not tgt_perms.embed_links:
            missing.append("target:embed_links")

        if missing:
            return await ctx.reply(f"❌ Missing permissions: {', '.join(missing)}.")

        # Resolve message reference
        msg = await self._resolve_message_by_ref(ctx, msg_ref)
        if not msg:
            return await ctx.reply("❌ Could not resolve the message.")

        # Skip system or webhook
        if msg.type != discord.MessageType.default or getattr(msg, "webhook_id", None):
            return await ctx.reply("⚠️ That message cannot be cloned (system/webhook).")

        # Build sanitized content
        author_display = msg.author.display_name if hasattr(msg.author, "display_name") else str(msg.author)
        sanitized = self._sanitize_content(msg.content or "", msg.guild)
        content = f"**{author_display}:** {sanitized}" if sanitized else f"**{author_display}**"

        # Prepare attachments
        files = []
        for att in msg.attachments:
            try:
                data = await att.read()
                files.append(discord.File(io.BytesIO(data), filename=att.filename))
            except Exception as e:
                # fallback link
                content += f"\n📎 Attachment not cloned: [{att.filename}]({att.url})"

        # Prepare embeds
        new_embeds = []
        for emb in msg.embeds:
            try:
                new_embeds.append(discord.Embed.from_dict(emb.to_dict()))
            except:
                pass

        # Split into chunks
        chunks = self._chunk_content(content, 2000)

        # Send in chunks
        try:
            for idx, chunk in enumerate(chunks):
                await tgt_channel.send(
                    content=chunk or None,
                    embeds=new_embeds if idx == 0 else None,
                    files=files if idx == 0 else None
                )

            await ctx.reply("✅ Message cloned successfully.", delete_after=10)

        except Exception as e:
            await ctx.reply(f"❌ Failed to clone message: `{e}`")







    # ------------------ canclone command ------------------
    @commands.is_owner()
    @commands.command(name="canclone")
    async def canclone(self, ctx: commands.Context, channel_ref: str, limit: int | None = None):
        """
        Count how many messages in a channel are clonable.
        Usage:
        ;canclone <channel | link | id | #mention>        -> scan full history
        ;countclone <channel | link | id | #mention> <n>    -> scan last n messages
        """
        channel = await self._resolve_channel_ref(ctx, channel_ref)
        if not channel:
            return await ctx.reply("❌ Could not resolve the channel.")

        if channel.type == discord.ChannelType.private:
            return await ctx.reply("❌ DM channels are not supported.")

        # permissions for that guild/channel
        bot_member = channel.guild.get_member(self.bot.user.id)
        perms = channel.permissions_for(bot_member)

        if not perms.read_message_history:
            return await ctx.reply("❌ I lack **Read Message History** in that channel.")

        # None => full history, else last `limit` messages
        history_limit = None if limit is None else max(1, limit)

        clonable = 0
        skipped = 0

        async for msg in channel.history(limit=history_limit):
            # same criteria as your clone command
            if msg.type != discord.MessageType.default:
                skipped += 1
                continue
            if getattr(msg, "webhook_id", None):
                skipped += 1
                continue
            # include bot + normal user messages
            clonable += 1

        scope = "entire history" if limit is None else f"last {history_limit} message(s)"
        await ctx.reply(
            f"📊 In **{channel.mention}** ({scope}):\n"
            f"• Clonable messages: `{clonable}`\n"
            f"• Skipped (system/webhook): `{skipped}`"
        )






    






    # ------------------ helper resolvers ------------------
    async def _resolve_channel_ref(self, ctx: commands.Context, ref: str) -> Optional[discord.TextChannel]:
        """
        Resolve channel by:
          - channel link https://discord.com/channels/<guild_id>/<channel_id>
          - raw channel id
          - channel mention like #name (returns same-channel if ambiguous)
        """
        ref = ref.strip()

        # channel link
        m = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)", ref)
        if m:
            guild_id = int(m.group(1))
            chan_id = int(m.group(2))
            with contextlib.suppress(Exception):
                return await self.bot.fetch_channel(chan_id)

        # channel mention <#id>
        m = re.fullmatch(r"<#(\d+)>", ref)
        if m:
            with contextlib.suppress(Exception):
                return await self.bot.fetch_channel(int(m.group(1)))

        # raw id
        if re.fullmatch(r"\d{6,22}", ref):
            with contextlib.suppress(Exception):
                return await self.bot.fetch_channel(int(ref))

        # fallback: try to interpret as name in current guild (#channel)
        if ctx.guild:
            normalized = ref.lstrip("#").lower()
            for ch in ctx.guild.text_channels:
                if ch.name.lower() == normalized:
                    return ch

        return None

    async def _resolve_channel_or_channel_from_message_ref(self, ctx: commands.Context, ref: str) -> Optional[discord.TextChannel]:
        """
        Resolve channel from:
         - message link https://discord.com/channels/<g>/<c>/<m>  (returns channel)
         - channel link/id
         - raw message id (interpreted in current channel)
        """
        # message link
        ref = ref.strip()
        m = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", ref)
        if m:
            guild_id, chan_id, msg_id = map(int, m.groups())
            with contextlib.suppress(Exception):
                ch = await self.bot.fetch_channel(chan_id)
                return ch

        # raw message id -> current channel
        if re.fullmatch(r"\d{6,22}", ref):
            # try to fetch in current channel
            with contextlib.suppress(Exception):
                try:
                    mid = int(ref)
                    await ctx.channel.fetch_message(mid)  # test existence
                    return ctx.channel
                except Exception:
                    pass

        # otherwise, attempt channel resolution
        return await self._resolve_channel_ref(ctx, ref)
    

    async def _resolve_message_by_ref(self, ctx: commands.Context, ref: str) -> discord.Message | None:
        """
        Resolve:
        - raw message ID in current channel
        - message link https://discord.com/channels/guild/channel/message
        """
        ref = ref.strip()

        # message link
        m = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", ref)
        if m:
            guild_id, chan_id, msg_id = map(int, m.groups())
            try:
                ch = await self.bot.fetch_channel(chan_id)
                return await ch.fetch_message(msg_id)
            except:
                return None

        # raw ID (current channel only)
        if re.fullmatch(r"\d{6,22}", ref):
            try:
                return await ctx.channel.fetch_message(int(ref))
            except:
                return None

        return None


    def _chunk_content(self, content: str, limit: int = 2000) -> list[str]:
        """Split content into <= limit-sized chunks for Discord."""
        if not content:
            return [""]
        return [content[i:i+limit] for i in range(0, len(content), limit)]
    
    def _sanitize_content(self, content: str, guild_src: discord.Guild | None) -> str:
        """
        Convert mentions to plain text:
          - user mentions -> @display_name or @id
          - role mentions -> @role_name or @id
          - channel mentions -> #channel_name or #id
          - neutralize @everyone/@here
          - convert custom emoji to :name:
        """
        if not content:
            return ""

        # neutralize everyone/here
        content = content.replace("@everyone", "@ everyone").replace("@here", "@ here")

        # user mentions <@123> or <@!123>
        def user_repl(m):
            uid = int(m.group("id"))
            if guild_src:
                member = guild_src.get_member(uid)
                if member:
                    return f"@{member.display_name}"
            return f"@{uid}"

        content = re.sub(r"<@!?(?P<id>\d+)>", user_repl, content)

        # role mentions <@&123>
        def role_repl(m):
            rid = int(m.group("id"))
            if guild_src:
                role = guild_src.get_role(rid)
                if role:
                    return f"@{role.name}"
            return f"@{rid}"

        content = re.sub(r"<@&(?P<id>\d+)>", role_repl, content)

        # channel mentions <#123>
        def chan_repl(m):
            cid = int(m.group("id"))
            if guild_src:
                ch = guild_src.get_channel(cid)
                if ch:
                    return f"#{ch.name}"
            return f"#{cid}"

        content = re.sub(r"<#(?P<id>\d+)>", chan_repl, content)

        # custom emoji <:name:id> or <a:name:id> -> :name:
        content = re.sub(r"<a?:([a-zA-Z0-9_]+):\d+>", r":\1:", content)

        return content



# setup
async def setup(bot: commands.Bot):
    await bot.add_cog(Clone(bot))