import os
import uuid
import tempfile
from typing import Callable, Awaitable, Any, Optional
import imageio_ffmpeg

import discord
from discord.ext import commands
from discord import app_commands

from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy.video.VideoClip import ImageClip
#from moviepy.video.fx.all import subclip as fx_subclip


SendFunc = Callable[..., Awaitable[Any]]


class Tools(commands.Cog):
    """General tools & utilities (GIF conversion, etc.)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------------------------------
    # Internal helpers
    # -------------------------------------------------

    @staticmethod
    def _guess_is_image(attachment: discord.Attachment) -> bool:
        """Best-effort check whether an attachment is an image."""
        if attachment.content_type and attachment.content_type.startswith("image"):
            return True

        # Fallback: use extension
        image_exts = {".png", ".jpg", ".jpeg", ".webp"}
        _, ext = os.path.splitext(attachment.filename.lower())
        return ext in image_exts

    async def _get_source_message_with_attachment(
        self, message: discord.Message
    ) -> Optional[discord.Message]:
        """
        Return the message that actually holds the attachment:
        - the message itself, if it has one
        - otherwise, the message it is replying to (if any & has attachment)
        """
        if message.attachments:
            return message

        if message.reference and message.reference.message_id:
            try:
                replied = await message.channel.fetch_message(
                    message.reference.message_id
                )
                if replied.attachments:
                    return replied
            except (discord.NotFound, discord.HTTPException):
                pass

        return None

    async def _make_gif_from_message(
        self,
        message: discord.Message,
        send_func: SendFunc,
    ) -> None:
        """
        Core GIF logic, shared by:
        - prefix command ;mkgif
        - right-click Apps → Make GIF (Helix)
        """

        src = await self._get_source_message_with_attachment(message)
        if src is None:
            await send_func(
                content="❌ I could not find any attachment on this message or the message it is replying to."
            )
            return

        attachment = src.attachments[0]

        tmp_dir = tempfile.gettempdir()
        _, in_ext = os.path.splitext(attachment.filename)
        if not in_ext:
            in_ext = ".bin"

        input_path = os.path.join(tmp_dir, f"{uuid.uuid4()}{in_ext}")
        output_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.gif")

        try:
            # Download the attachment
            await attachment.save(input_path)

            # Image → fixed duration GIF
                        # Image → fixed duration GIF
# Image → fixed duration
            if self._guess_is_image(attachment):
                clip = ImageClip(input_path)
                if hasattr(clip, "with_duration"):
                    clip = clip.with_duration(4)
                else:
                    clip = clip.set_duration(4)

            else:
                # -------- VIDEO TRIMMING (ffmpeg) ----------
                trimmed_input_path = os.path.join(tmp_dir, f"{uuid.uuid4()}_trimmed.mp4")

                try:
                    ffmpeg_binary = imageio_ffmpeg.get_ffmpeg_exe()
                    cmd = [
                        ffmpeg_binary,
                        "-i", input_path,
                        "-t", "8",       # trim first 8 seconds
                        "-c", "copy",    # fast, no re-encode
                        trimmed_input_path
                    ]

                    import subprocess
                    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                    clip = VideoFileClip(trimmed_input_path)

                except Exception:
                    # fallback to full clip
                    clip = VideoFileClip(input_path)


                except Exception as e:
                    # Fallback: load whole video
                    clip = VideoFileClip(input_path)


            # Write GIF (12 fps)
            clip.write_gif(output_path, fps=12)
            clip.close()

            file = discord.File(output_path, filename="helix.gif")

            try:
                await send_func(
                    content="✅ Here is your GIF:",
                    file=file,
                )
            except discord.HTTPException as http_err:
                # Likely too large for Discord
                await send_func(
                    content=(
                        "⚠️ I made the GIF but Discord would not let me upload it "
                        "(probably too large). Try a shorter or smaller file.\n"
                        f"Details: `{http_err}`"
                    )
                )

        except Exception as e:
            await send_func(
                content=f"⚠️ Something went wrong while making the GIF: `{e}`"
            )

        finally:
            for path in (input_path, output_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass

    # -------------------------------------------------
    # Text command version: ;mkgif / ;makegif / ;togif
    # -------------------------------------------------

    @commands.command(name="mkgif", aliases=["makegif", "togif"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def mkgif(self, ctx: commands.Context) -> None:
        """
        Convert an image or a short video to a GIF.

        Usage:
            ;mkgif        (with an attachment)
            ;mkgif        (replying to a message that has media)
        """
        await ctx.typing()

        async def send_func(**kwargs: Any) -> None:
            await ctx.send(**kwargs)

        await self._make_gif_from_message(ctx.message, send_func)


# -------------------------------------------------
# Right-click Apps version: "Make GIF (Helix)"
# -------------------------------------------------

@app_commands.context_menu(name="Make GIF (Helix)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def mkgif_context(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    """
    Message context menu:
    Right-click message → Apps → Make GIF (Helix)
    """
    await interaction.response.defer(thinking=True)

    # Get the Tools cog instance
    tools_cog = interaction.client.get_cog("Tools")
    if tools_cog is None:
        await interaction.followup.send(
            "❌ Tools cog is not loaded, cannot create GIF.",
            ephemeral=True,
        )
        return

    async def send_func(**kwargs: Any) -> None:
        # Using followup because we already deferred
        await interaction.followup.send(**kwargs)

    # Reuse the cog helper
    await tools_cog._make_gif_from_message(message, send_func)


async def setup(bot: commands.Bot) -> None:
    # Add the cog
    await bot.add_cog(Tools(bot))
    # Register the context menu with the bot's app command tree
    bot.tree.add_command(mkgif_context)
