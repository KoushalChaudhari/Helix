import aiohttp
import html
import random
import re, io, os
import asyncio
from datetime import datetime, timezone
from discord.ext import commands
import discord
from cogs.core import mkembed, COLORS
from config import TENOR_KEY
from PIL import Image, ImageDraw, ImageFont
import requests
import textwrap




TENOR_SEARCH = "https://tenor.googleapis.com/v2/search"
TRIVIA_EMOJIS = ["🇦", "🇧", "🇨", "🇩"]


def _extract_message_id(arg: str) -> int | None:
    # Accept raw ID or message link
    arg = arg.strip().strip("<>").replace("\n", "")
    # Full message link form: https://discord.com/channels/guild_id/channel_id/message_id
    if "discord.com/channels/" in arg:
        parts = arg.split("/")
        try:
            return int(parts[-1])
        except Exception:
            return None
    # raw id
    try:
        return int(arg)
    except Exception:
        return None


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trivia_sessions = {}  # channel_id -> {question, correct, options, expires_at}
        if not hasattr(bot, 'active_polls'):
            bot.active_polls = {}  # message_id -> PollView instance





# ========================= Reaction Listener ==================
    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """Handle trivia reactions."""
        # Ignore bot reactions
        if user.bot:
            return

        # Find the active trivia session in this channel
        session = self.trivia_sessions.get(reaction.message.channel.id)
        if not session:
            return

        # Ensure it's for the current trivia message
        if reaction.message.id != session["msg_id"]:
            return

        emoji = str(reaction.emoji)
        if emoji not in ["🇦", "🇧", "🇨", "🇩"]:
            return

        # Prevent same user from answering multiple times
        if user.id in session["answered"]:
            await reaction.message.channel.send(f"{user.mention}, you already answered!", delete_after=5)
            return

        session["answered"].add(user.id)

        idx = ["🇦", "🇧", "🇨", "🇩"].index(emoji)
        selected = session["options"][idx]
        correct = session["correct"]

        # Check answer
        if selected == correct:
            await reaction.message.channel.send(
                embed=mkembed(
                    "✅ Correct!",
                    f"{user.mention} got it right! 🎉",
                    COLORS["SUCCESS"]
                )
            )
            # Optional: end trivia once someone gets it right
            self.trivia_sessions.pop(reaction.message.channel.id, None)
        else:
            await reaction.message.channel.send(
                embed=mkembed(
                    "❌ Wrong!",
                    f"{user.mention} chose **{selected}** — correct answer was **{correct}**.",
                    COLORS["ERROR"]
                )
            )




# =============================================================
#                         GIF COMMAND
# =============================================================

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




# =============================================================
#                        MEME COMMAND
# =============================================================
    @commands.command(name="meme")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def meme(self, ctx: commands.Context):
        """Fetch and display a random meme from Reddit."""
        api_url = "https://meme-api.com/gimme"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=10) as response:
                    if response.status != 200:
                        return await ctx.reply(
                            embed=mkembed(
                                "❌ API Error",
                                "Couldn't fetch a meme right now. Try again later.",
                                COLORS["ERROR"]
                            )
                        )
                    data = await response.json()
        except asyncio.TimeoutError:
            return await ctx.reply(
                embed=mkembed(
                    "⌛ Timeout",
                    "The meme API took too long to respond.",
                    COLORS["WARNING"]
                )
            )
        except Exception as e:
            return await ctx.reply(
                embed=mkembed(
                    "⚠️ Error",
                    f"Something went wrong fetching a meme.\n`{type(e).__name__}: {e}`",
                    COLORS["ERROR"]
                )
            )

        # Extract data
        title = data.get("title", "Untitled")
        subreddit = data.get("subreddit", "memes")
        author = data.get("author", "unknown")
        post_link = data.get("postLink", "")
        image_url = data.get("url", None)

        # Create the embed
        em = mkembed(
            f"🤣 {title}",
            f"**Subreddit:** r/{subreddit}\n**Posted by:** u/{author}",
            COLORS["INFO"]
        )
        if image_url:
            em.set_image(url=image_url)
        if post_link:
            em.add_field(name="Post Link", value=f"[View on Reddit]({post_link})", inline=False)

        await ctx.reply(embed=em)
# Meme command error handler for cooldown
    @meme.error
    async def meme_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                embed=mkembed(
                    "⏳ Slow down!",
                    f"Try again in **{error.retry_after:.1f}s**.",
                    COLORS["WARNING"]
                ),
                delete_after=4
            )
            error.handled = True  # type: ignore





# =============================================================
#                       TRIVIA COMMAND
# =============================================================
    @commands.command(name="trivia")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def trivia(self, ctx: commands.Context, *, args: str | None = None):
        """Start a multiple-choice trivia question (react to answer)."""
        if ctx.channel.id in self.trivia_sessions:
            return await ctx.reply(embed=mkembed(
                "❌ Trivia Already Active",
                "Finish the current round before starting a new one.",
                COLORS["WARNING"]
            ))

        # --- Difficulty & category parsing (keep your existing logic here) ---
        args = (args or "").lower()
        params = {"amount": 1, "type": "multiple"}
        category_map = {
            # 🌟 Pop Culture & Celebrities
            "celeb": 26,
            "celebs": 26,
            "celebrity": 26,
            "celebrities": 26,
            "pop": 26,
            "popculture": 26,
            "culture": 26,
            "hollywood": 26,
            "famous": 26,
            "actor": 26,
            "actress": 26,

            # 🎵 Music, Hip Hop & Entertainment
            "music": 12,
            "songs": 12,
            "song": 12,
            "hiphop": 12,
            "rap": 12,
            "popmusic": 12,
            "artist": 12,
            "singer": 12,
            "band": 12,

            # 🎬 Movies, TV, and Games
            "movie": 11,
            "movies": 11,
            "film": 11,
            "cinema": 11,
            "tv": 14,
            "television": 14,
            "show": 14,
            "series": 14,
            "anime": 31,
            "manga": 31,
            "cartoon": 32,
            "animation": 32,
            "games": 15,
            "videogames": 15,

            # 📚 Knowledge & Science
            "general": 9,
            "gk": 9,
            "trivia": 9,
            "random": 9,
            "science": 17,
            "nature": 17,
            "computers": 18,
            "tech": 18,
            "technology": 18,
            "math": 19,
            "mathematics": 19,
            "history": 23,
            "geography": 22,

            # ⚽ Lifestyle & Misc
            "sports": 21,
            "animals": 27,
            "cars": 28,
            "vehicles": 28,
            "comics": 29,
            "superhero": 29,
            "art": 25,
        }
        
        if "easy" in args:
                params["difficulty"] = "easy"
        elif "medium" in args:
            params["difficulty"] = "medium"
        elif "hard" in args:
            params["difficulty"] = "hard"

        # Check for category match
        selected_category = None
        for k, v in category_map.items():
            if k in args:
                selected_category = v
                break

        if selected_category:
            params["category"] = selected_category
        else:
            # If the user specified something that isn't supported (and not just difficulty)
            if args and not any(x in args for x in ("easy", "medium", "hard")):
                return await ctx.reply(embed=mkembed(
                    "❌ Unsupported Category",
                    "That category isn't supported.\nUse `;trivia categories` to see available topics.",
                    COLORS["ERROR"]
                ))


        # --- Fetch from OpenTDB ---
        async with aiohttp.ClientSession() as session:
            async with session.get("https://opentdb.com/api.php", params=params) as res:
                data = await res.json()

        if not data["results"]:
            return await ctx.reply(embed=mkembed(
                "⚠️ No Questions Found",
                "Try another category or difficulty.",
                COLORS["WARNING"]
            ))

        q = data["results"][0]
        question = html.unescape(q["question"])
        correct = html.unescape(q["correct_answer"])
        options = [html.unescape(x) for x in q["incorrect_answers"]] + [correct]
        random.shuffle(options)

        desc = "\n".join(f"{TRIVIA_EMOJIS[i]}  **{opt}**" for i, opt in enumerate(options))
        meta = f"Category: `{html.unescape(q['category'])}` • Difficulty: `{q['difficulty'].title()}`"

        em = mkembed("🧠 Trivia", f"{question}\n\n{desc}\n\n{meta}", COLORS["INFO"])
        msg = await ctx.reply(embed=em)

        # add A-D reactions
        for i in range(len(options)):
            await msg.add_reaction(TRIVIA_EMOJIS[i])

        # save session
        self.trivia_sessions[ctx.channel.id] = {
            "msg_id": msg.id,
            "correct": correct,
            "options": options,
            "answered": set(),  # track users who already responded
        }

        # auto-expire in 2 min
        async def expire():
            await asyncio.sleep(120)
            if self.trivia_sessions.get(ctx.channel.id, {}).get("msg_id") == msg.id:
                self.trivia_sessions.pop(ctx.channel.id, None)
                await ctx.send(embed=mkembed("⏲️ Trivia Expired", "Time’s up!", COLORS["WARNING"]))
        asyncio.create_task(expire())





# ========================= Trivia categories ==================
    @commands.command(name="trivia_categories", aliases=["triviacats"])
    async def trivia_categories(self, ctx):
        """Show available trivia categories."""
        cats = [
            # 🌟 Pop Culture & Celebrities
            "celeb",
            "celebs",
            "celebrity",
            "celebrities",
            "pop",
            "popculture",
            "culture",
            "hollywood",
            "famous",
            "actor",
            "actress",

            # 🎵 Music, Hip Hop & Entertainment
            "music",
            "songs",
            "song",
            "hiphop",
            "rap",
            "popmusic",
            "artist",
            "singer",
            "band",

            # 🎬 Movies, TV, and Games
            "movie",
            "movies",
            "film",
            "cinema",
            "tv",
            "television",
            "show",
            "series",
            "anime",
            "manga",
            "cartoon",
            "animation",
            "games",
            "videogames",

            # 📚 Knowledge & Science
            "general",
            "gk",
            "trivia",
            "random",
            "science",
            "nature",
            "computers",
            "tech",
            "technology",
            "math",
            "mathematics",
            "history",
            "geography",

            # ⚽ Lifestyle & Misc
            "sports",
            "animals",
            "cars",
            "vehicles",
            "comics",
            "superhero",
            "art",
        ]
        em = mkembed("🎯 Trivia Categories", "\n".join(f"• {c}" for c in cats), COLORS["INFO"])
        await ctx.reply(embed=em)




# =============================================================
#                         POLL COMMAND
# =============================================================
    # ======================== Button Poll (new syntax) =========================
    @commands.command(name="poll")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def poll(self, ctx: commands.Context, *, text: str):
        """
        Create a button-based poll.
        Format:
          ;poll <question> op: option1, option2, option3, ...
        Example:
          ;poll Best language op: Python, Java, C++
        """
        # Split at 'op:' to separate question and options
        if "op:" not in text.lower():
            return await ctx.reply(embed=mkembed(
                "❌ Invalid Format",
                "Use: `;poll <question> op: <option1>, <option2>, ...` \n\n Example: `;poll Best color? op: Red, Blue, Green`",
                COLORS["ERROR"]
            ))

        parts = re.split(r'\s+op:\s+', text, flags=re.IGNORECASE)
        if len(parts) != 2:
            return await ctx.reply(embed=mkembed(
                "❌ Invalid Format",
                "Make sure you include `op:` before listing options.",
                COLORS["ERROR"]
            ))

        question, opts = parts
        question = question.strip()
        options = [o.strip() for o in opts.split(",") if o.strip()]

        if not question:
            return await ctx.reply(embed=mkembed(
                "⚠️ Missing Question",
                "Please include a poll question before `op:`.",
                COLORS["WARNING"]
            ))

        if not (2 <= len(options) <= 5):
            return await ctx.reply(embed=mkembed(
                "⚠️ Need 2–5 options",
                "Please provide between **2** and **5** options.",
                COLORS["WARNING"]
            ))

        # Create and send poll
        view = PollView(question, options, author_id=ctx.author.id)
        em = mkembed("📊 Poll", f"**{question}**\n\nClick a button to vote!", COLORS["INFO"])
        msg = await ctx.reply(embed=em, view=view)
        view.message = msg

        # Keep poll running indefinitely
        self.bot.active_polls[msg.id] = view  # register active poll


    @poll.error
    async def _poll_cooldown(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=mkembed(
                "⏳ Slow down!",
                f"Try again in **{error.retry_after:.1f}s**.",
                COLORS["WARNING"]
            ), delete_after=4)



# ================== End Poll =========================
    @commands.command(name="endpoll")
    @commands.has_permissions(manage_messages=True)
    async def endpoll(self, ctx: commands.Context, *, message_ref: str):
        """
        End an active poll by message ID or link.
        Usage:
        ;endpoll 123456789012345678
        ;endpoll https://discord.com/channels/..../<message_id>
        """
        mid = _extract_message_id(message_ref)
        if not mid:
            return await ctx.reply(embed=mkembed(
                "⚠️ Invalid Message Reference",
                "Provide a valid **message ID** or **message link**.",
                COLORS["WARNING"]
            ))

        view: PollView | None = self.bot.active_polls.get(mid)
        if not view:
            # Try fetching the message in this channel as a convenience
            # (we can’t reconstruct the view, but we can tell the user)
            try:
                msg = await ctx.channel.fetch_message(mid)
                # If we got here, the poll likely lives in another view instance or bot restarted
                return await ctx.reply(embed=mkembed(
                    "⚠️ Poll Not Active",
                    "I found a message with that ID in this channel, but it’s not an **active poll**.\n"
                    "Polls only stay active until I’m restarted or until `;endpoll` is used while I’m online.",
                    COLORS["WARNING"]
                ))
            except Exception:
                return await ctx.reply(embed=mkembed(
                    "⚠️ Poll Not Found",
                    "I couldn’t find an **active** poll with that message ID.\n"
                    "Make sure the poll was created **after** the last bot restart.",
                    COLORS["WARNING"]
                ))

        # Optional: restrict closure to creator or mods
        if (ctx.author.id != view.author_id and
            not ctx.author.guild_permissions.manage_messages):
            return await ctx.reply(embed=mkembed(
                "🚫 Not Allowed",
                "Only the poll creator or moderators (Manage Messages) can end this poll.",
                COLORS["ERROR"]
            ))

        # End it and show a summary here as well
        try:
            total, summary = await view.end_poll()
        finally:
            self.bot.active_polls.pop(mid, None)

        result_embed = mkembed(
            "📊 Poll Ended",
            f"The poll **{view.question}** has been closed.",
            COLORS["INFO"]
        )
        result_embed.add_field(name="Results", value=summary, inline=False)
        await ctx.reply(embed=result_embed)

    @endpoll.error
    async def endpoll_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(embed=mkembed(
                "🚫 Missing Permission",
                "You need **Manage Messages** to end polls.",
                COLORS["ERROR"]
            ))
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(embed=mkembed(
                "⚠️ Invalid Format",
                "Usage: `;endpoll <message_id_or_link>`",
                COLORS["WARNING"]
            ))





# =============================================================
#                       8Ball COMMAND
# =============================================================
    @commands.command(name="8ball", aliases=["eightball"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def eight_ball(self, ctx: commands.Context, *, question: str | None = None):
        """Ask the Magic 8-Ball your burning question."""
        if not question:
            return await ctx.reply(embed=mkembed(
                "🎱 Ask the 8-Ball",
                "Usage: `;8ball <your question>`\n\nExample: `;8ball Will Helix take over the world?`",
                COLORS["WARNING"]
            ))

        responses = [
            # ✅ Polite / Encouraging
            "Absolutely! Believe in yourself 💫",
            "Yes — and the universe agrees with you.",
            "Without a doubt, my friend.",
            "Good vibes say yes.",
            "100% certain. Don't even question it.",
            "It looks promising — go for it!",
            "You got this.",

            # 🤔 Neutral / Philosophical
            "Ask again when Mercury isn’t in retrograde.",
            "I’m still buffering… try again later.",
            "Maybe, maybe not. Schrödinger’s answer.",
            "Unclear. My circuits are conflicted.",
            "Hmm… I’d say it’s 50/50 at best.",
            "You already know the answer, don’t you?",
            "Let fate decide — flip a coin.",

            # ❌ Classic Negatives
            "Nope. Not even close.",
            "Outlook not so good.",
            "Definitely not.",
            "Don’t count on it.",
            "Very doubtful.",
            "Error 404: Hope not found.",
            "My sources say no. And they sound confident.",

            # 🗣️ Witty / Rude / Chaotic
            "Why are you even asking me that?",
            "Bold of you to assume I care.",
            "Nah fam, that’s a hard pass.",
            "Sure, in another timeline maybe.",
            "If stupidity was currency, you’d be rich.",
            "Ask again when your brain’s fully charged.",
            "I’d say yes, but I don’t like lying.",
            "No, but nice try.",
            "Who hurt you?",
            "Don’t make me roll my eyes in binary.",
            "I’ve seen worse ideas. Not many, though.",
            "Ask a bot or something instead. Oh wait… that’s me.",
            "Absolutely! Just kidding. No.",
            "The stars say yes, but your life choices say no.",
            "Sure, if you’re into disappointment.",
            "Yikes. That’s a question you shouldn’t have asked.",
            "Ask again when your breath ain't stinky 🤢",
            "Ask again in 6-7 minutes",
            "Sybau 💔🥀",
            "STFUIFLYWLFEAEYFH 🤬",
            "https://tenor.com/view/jgmm-monkey-think-monkey-meme-ponder-monkey-idea-gif-6401133862108294696"
        ]

        response = random.choice(responses)
        em = mkembed(
            "🎱 The Magic 8-Ball Speaks",
            f"**Question:** {question}\n\n**Answer:** {response}",
            COLORS["INFO"]
        )
        await ctx.reply(embed=em)

    @eight_ball.error
    async def _8ball_cooldown(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=mkembed(
                "⏳ Slow down!",
                f"The 8-Ball is tired of your questions. Try again in **{error.retry_after:.1f}s**.",
                COLORS["WARNING"]
            ), delete_after=4)





# ============================================================
#                      DICE ROLL COMMAND
# ============================================================
    @commands.command(name="roll", aliases=["dice"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def roll(self, ctx: commands.Context, *, formula: str | None = None):
        """
        Roll dice using standard RPG format [NdM][+/-modifier].
        Examples:
          ;roll         → 1d6
          ;roll 2d6     → two six-sided dice
          ;roll 1d20+5  → one d20 with +5 modifier
        """
        import re, random

        # Default to 1d6 if no argument
        if not formula:
            formula = "1d6"

        match = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", formula.replace(" ", ""))
        if not match:
            return await ctx.reply(embed=mkembed(
                "⚠️ Invalid Format",
                "Use standard dice notation like `2d6`, `1d20+5`, or `3d10-2`.",
                COLORS["WARNING"]
            ))

        num_dice = int(match.group(1) or 1)
        sides = int(match.group(2))
        modifier = int(match.group(3) or 0)

        # Limit sanity ranges
        if num_dice > 100 or sides > 1000:
            return await ctx.reply(embed=mkembed(
                "🚫 Too Many Dice",
                "Try rolling fewer dice or smaller sides (max 100 dice, 1000 sides).",
                COLORS["ERROR"]
            ))

        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        total = sum(rolls) + modifier

        # Create readable result text
        roll_text = ", ".join(map(str, rolls))
        mod_text = f" {modifier:+}" if modifier else ""
        result_text = f"({roll_text}){mod_text}"

        em = mkembed(
            "🎲 Dice Roll",
            f"**Input:** `{formula}`\n**Rolls:** {result_text}\n\n**Total:** 🎯 **{total}**",
            COLORS["INFO"]
        )
        await ctx.reply(embed=em)

    @roll.error
    async def roll_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=mkembed(
                "⏳ Slow down!",
                f"Let the dice cool for **{error.retry_after:.1f}s**.",
                COLORS["WARNING"]
            ), delete_after=4)





# =============================================================
#                   ROCK-PAPER-SCISSORS COMMAND
# =============================================================
    @commands.command(name="rps", aliases=["rockpaperscissors"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rps(self, ctx: commands.Context, choice: str | None = None):
        """
        Play rock-paper-scissors with Helix.
        Usage:
          ;rps <rock|paper|scissors>
          Shortcuts: r / p / s
        """
        import random

        # Supported moves + shortcuts
        valid_moves = {
            "rock": "🪨 Rock",
            "paper": "📄 Paper",
            "scissors": "✂️ Scissors"
        }
        aliases = {
            "r": "rock",
            "p": "paper",
            "s": "scissors"
        }

        # Validate and normalize input
        if not choice:
            return await ctx.reply(embed=mkembed(
                "🎮 Rock Paper Scissors",
                "Usage: `;rps <rock|paper|scissors>`\nShortcuts: `r`, `p`, `s`",
                COLORS["WARNING"]
            ))

        choice = choice.lower().strip()
        user_move = aliases.get(choice, choice)  # map shorthand to full name

        if user_move not in valid_moves:
            return await ctx.reply(embed=mkembed(
                "⚠️ Invalid Choice",
                "Pick one of: `rock (r)`, `paper (p)`, or `scissors (s)`.",
                COLORS["WARNING"]
            ))

        # Bot chooses randomly
        bot_move = random.choice(list(valid_moves.keys()))

        # Determine outcome
        outcomes = {
            ("rock", "scissors"): "win",
            ("paper", "rock"): "win",
            ("scissors", "paper"): "win",
            ("scissors", "rock"): "lose",
            ("rock", "paper"): "lose",
            ("paper", "scissors"): "lose"
        }

        if user_move == bot_move:
            result = "draw"
        else:
            result = outcomes.get((user_move, bot_move), "lose")

        # Witty remarks by result
        if result == "win":
            remarks = [
                "You got lucky this time. 😏",
                "Okay okay… don't get too cocky now!",
                "No way… did you actually beat me?! 😤",
                "Fine. You win this round.",
                "Ugh, I blinked!",
                "Damn bruh you hacking or something?",
                "I let you win, don't tell anyone. 🤫",
                "Nah! You definitely cheated!",
                "OW HELL NAW!",
                "Im so cooked 💔"
            ]
            color = COLORS["SUCCESS"]
            title = "🏆 You Win!"
        elif result == "lose":
            remarks = [
                "LMAO! Nice try, human. 😂",
                "You're so chopped gng lock in 💔🥀",
                "Did you even try?",
                "I could do this all day. 😎",
                "Skill issue.",
                "Lock in bruh 💔🥀",
                "Just quit gng, you're so bad at ts 💔🥀",
                "LMAO! COPE!!!"
            ]
            color = COLORS["ERROR"]
            title = "💀 You Lose!"
        else:
            remarks = [
                "A draw? How anticlimactic.",
                "We’re evenly matched. For now. 🤝",
                "Well that was boring.",
                "Hmm, let’s call it even.",
                "Guess playing at a room temperature IQ won't get us anywhere.",
                "LOL! Bro played the mirror card!",
                "AYO RUN IT BACK!"
            ]
            color = COLORS["INFO"]
            title = "🤝 It's a Draw!"

        # Construct the response embed
        em = mkembed(
            title,
            (
                f"**Your Move:** {valid_moves[user_move]}\n"
                f"**Helix’s Move:** {valid_moves[bot_move]}\n\n"
                f"{random.choice(remarks)}"
            ),
            color
        )

        await ctx.reply(embed=em)

    @rps.error
    async def rps_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(embed=mkembed(
                "⏳ Easy there, champ!",
                f"Try again in **{error.retry_after:.1f}s** — I need to recharge my scissors. ✂️",
                COLORS["WARNING"]
            ), delete_after=4)




# ===================================================================
#                        Quote Command
# ===================================================================

    # ------------------ Quote command ------------------
    @commands.command(name="quote")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def quote_cmd(self, ctx: commands.Context, *, text: str = None):
        """
        Generate a quote image.
        Usage:
            ;quote <text>
            (or reply to a message with ;quote — will use the replied message's text and author)
        """
        # If the command is used as a reply and no text arg was provided,
        # use the replied-to message's content as quote text and its author/avatar.
        avatar_url = str(ctx.author.display_avatar.replace(size=1024).url)
        author_name = f"- {ctx.author.display_name}"
        author_tag = f"@{ctx.author.name}"

        if not text and ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                # prefer replied message's content if present
                if ref_msg.content and ref_msg.content.strip():
                    text = ref_msg.content.strip()
                    avatar_url = str(ref_msg.author.display_avatar.replace(size=1024).url)
                    author_name = f"- {ref_msg.author.display_name}"
                    author_tag = f"@{ref_msg.author.name}"
            except Exception:
                # fallback to default behaviour (use the invoker's text/avatar)
                pass

        if not text or not text.strip():
            await ctx.reply("❌ Please provide text, or reply to a message containing the text you want quoted.")
            return

        try:
            img_bytes = await self._generate_quote_image_async(
                quote_text=text,
                author_name=author_name,
                author_tag=author_tag,
                avatar_url=avatar_url,
                base_font_path="assets/fonts/YourNiceFont.ttf"  # adjust if you have a font
            )
            await ctx.send(file=discord.File(img_bytes, filename="quote.png"))
        except Exception as e:
            await ctx.reply(f"⚠️ Failed to create quote image: `{type(e).__name__}: {e}`")

    # ------------------ Helpers (static/async-friendly) ------------------

    @staticmethod
    def _download_image_to_pil(url: str) -> Image.Image:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGBA")

    @staticmethod
    def _cover_resize_and_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        src_w, src_h = img.size
        scale = max(target_w / src_w, target_h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = img.resize((new_w, new_h), resample=Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))

    @staticmethod
    def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int):
        # returns list of lines that fit in max_width
        words = text.split()
        if not words:
            return []
        lines = []
        current = words[0]
        for w in words[1:]:
            test = current + " " + w
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                lines.append(current)
                current = w
        lines.append(current)
        return lines

    async def _generate_quote_image_async(
        self,
        *,
        quote_text: str,
        author_name: str,
        author_tag: str,
        avatar_url: str,
        canvas_size=(1280, 720),
        left_fraction=0.35,
        base_font_path: str = None,
    ) -> io.BytesIO:
        """
        Async wrapper that runs the synchronous image generation in a threadpool
        to avoid blocking the event loop.
        """
        # Run blocking PIL/requests code in a thread
        return await asyncio.to_thread(
            self._generate_quote_image,
            quote_text,
            author_name,
            author_tag,
            avatar_url,
            canvas_size,
            left_fraction,
            base_font_path,
        )

    def _generate_quote_image(
        self,
        quote_text: str,
        author_name: str,
        author_tag: str,
        avatar_url: str,
        canvas_size=(1280, 720),
        left_fraction=0.35,            # ignored now (we compute left_w to fit whole pfp)
        base_font_path: str = None,
    ) -> io.BytesIO:
        """
        Synchronous image generation. Returns BytesIO (PNG).
        Left area will be sized to fit a *full square* avatar (height x height),
        so the avatar isn't cropped out. Right side is a tinted gradient based
        on avatar's dominant color transitioning to dark.
        """
        W, H = canvas_size

        # Make left width large enough to hold the entire square avatar:
        # left_w = min(H, int(W * 0.6)) ensures we don't hog the whole canvas
        left_w = min(H, int(W * 0.6))
        right_w = W - left_w

        # paddings for text area
        pad_x = 60
        pad_top = 110
        usable_w = right_w - 2 * pad_x
        if usable_w < 200:
            # fallback to avoid extreme cases
            pad_x = 40
            usable_w = right_w - 2 * pad_x

        # Load avatar (blocking call; that's why we call this in a thread)
        try:
            pfp = self._download_image_to_pil(avatar_url)
        except Exception:
            pfp = Image.new("RGBA", (H, H), (80, 80, 80, 255))  # square fallback

        # ---------------------------------------------------------------------
        # Create a single background where the PFP is positioned so its center
        # appears inside the left square (left_w x H). This avoids any seam.
        # ---------------------------------------------------------------------
        src_w, src_h = pfp.size

        # Resize pfp to cover the canvas (scale up so no blank)
        scale = max(W / src_w, H / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        resized = pfp.resize((new_w, new_h), resample=Image.LANCZOS)

        # Determine desired center: place avatar center in the center of left square
        desired_center_x = left_w // 2
        desired_center_y = H // 2

        # Center of the resized image
        src_center_x = new_w / 2
        src_center_y = new_h / 2

        # Compute crop so src_center maps to desired_center
        crop_left = int(src_center_x - desired_center_x)
        crop_top = int(src_center_y - desired_center_y)

        # Clamp crop rectangle to valid bounds
        crop_left = max(0, min(crop_left, new_w - W))
        crop_top = max(0, min(crop_top, new_h - H))

        bg_full = resized.crop((crop_left, crop_top, crop_left + W, crop_top + H))

        # Build canvas and paste single background (no duplicate left paste)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        canvas.paste(bg_full, (0, 0))

        # ---------------------------------------------------------------------
        # Compute dominant color of the avatar for the color-driven gradient
        # ---------------------------------------------------------------------
        def _dominant_color(img: Image.Image):
            # quick downscale and common color pick
            small = img.convert("RGB").resize((32, 32))
            pixels = list(small.getdata())
            from collections import Counter
            ctr = Counter(pixels)
            most = ctr.most_common(1)[0][0]
            return most  # (r,g,b)

        dom_color = _dominant_color(pfp if pfp.mode == "RGB" or pfp.mode == "RGBA" else pfp.convert("RGB"))

        # ---------------------------------------------------------------------
        # Build a horizontal gradient overlay for the RIGHT area,
        # going from transparent (near left_w) to colored dark (near W).
        # We'll create an RGBA overlay and paste it with itself as mask.
        # ---------------------------------------------------------------------
        overlay = Image.new("RGBA", (right_w, H), (0, 0, 0, 0))
        r_dom, g_dom, b_dom = dom_color

        # We'll make the color tint start subtle and increase to a mostly-dark tone.
        for x in range(right_w):
            t = x / max(right_w - 1, 1)  # 0..1 across right area
            # color fades linearly toward black for RGB
            r = int(r_dom * (1 - t))
            g = int(g_dom * (1 - t))
            b = int(b_dom * (1 - t))
            # alpha increases so the tint gets stronger to the right
            alpha = int(200 * t)  # top alpha about 200/255
            # create a 1px wide vertical stripe
            stripe = Image.new("RGBA", (1, H), (r, g, b, alpha))
            overlay.paste(stripe, (x, 0))

        # Composite the overlay onto the right side of canvas
        canvas.paste(overlay, (left_w, 0), overlay)

        # --- Darken ONLY the background (30%) before adding text ---
        darken = Image.new("RGBA", canvas.size, (0, 0, 0, int(255 * 0.40)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), darken)

        draw = ImageDraw.Draw(canvas)

        # -------------------------
        # Font loader (same as before)
        # -------------------------
        def load_font(size):
            if base_font_path and os.path.exists(base_font_path):
                try:
                    return ImageFont.truetype(base_font_path, size=size)
                except Exception:
                    pass
            try:
                return ImageFont.truetype("arial.ttf", size=size)
            except Exception:
                return ImageFont.load_default()

        # -------------------------
        # Adaptive font sizing (same logic, using usable_w)
        # -------------------------
        start_font_size = 140
        min_font_size = 48
        quote_font = None
        lines = []
        used_font_size = start_font_size

        for size in range(start_font_size, min_font_size - 1, -6):
            f = load_font(size)
            test_lines = self._wrap_text(draw, quote_text, f, usable_w)
            if test_lines:
                bbox = f.getbbox("Ay")
                line_h = (bbox[3] - bbox[1]) + int(size * 0.12)
                block_h = line_h * len(test_lines)
            else:
                line_h = f.getbbox("Ay")[3] - f.getbbox("Ay")[1]
                block_h = line_h

            reserved_for_author = int(size * 1.2) + int(size * 0.6) + 20
            max_allowed_h = H - (pad_top * 2) - reserved_for_author

            if block_h <= max_allowed_h and len(test_lines) <= 6:
                quote_font = f
                lines = test_lines
                used_font_size = size
                break

        if quote_font is None:
            used_font_size = min_font_size
            quote_font = load_font(used_font_size)
            lines = self._wrap_text(draw, quote_text, quote_font, usable_w)
            bbox = quote_font.getbbox("Ay")
            line_h = (bbox[3] - bbox[1]) + int(used_font_size * 0.12)

        bbox = quote_font.getbbox("Ay")
        line_h = (bbox[3] - bbox[1]) + int(used_font_size * 0.12)
        block_h = line_h * len(lines)

        author_size = max(int(used_font_size * 0.45), 20)
        tag_size = max(int(used_font_size * 0.28), 14)
        author_font = load_font(author_size)
        tag_font = load_font(tag_size)

        text_start_y = (H - block_h) // 2
        text_start_y = max(pad_top, text_start_y - 20)

        # center lines inside right area
        x_center = left_w + (right_w // 2)
        current_y = text_start_y
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=quote_font)
            w_line = bbox[2] - bbox[0]
            x = x_center - (w_line // 2)
            draw.text((x, current_y), line, font=quote_font, fill=(255, 255, 255, 255))
            current_y += line_h

        # author & tag
        current_y += 14
        if author_name:
            bbox = draw.textbbox((0, 0), author_name, font=author_font)
            w_author = bbox[2] - bbox[0]
            x_author = x_center - (w_author // 2)
            draw.text((x_author, current_y), author_name, font=author_font, fill=(230, 230, 230, 255))
            current_y += int(author_size * 1.05)

        if author_tag:
            bbox = draw.textbbox((0, 0), author_tag, font=tag_font)
            w_tag = bbox[2] - bbox[0]
            x_tag = x_center - (w_tag // 2)
            draw.text((x_tag, current_y), author_tag, font=tag_font, fill=(180, 180, 180, 255))

        # watermark
        watermark = "Made with Helix"
        wm_font = load_font(20)
        bbox = draw.textbbox((0, 0), watermark, font=wm_font)
        w_wm = bbox[2] - bbox[0]
        draw.text((W - w_wm - 14, H - 28), watermark, font=wm_font, fill=(150, 150, 150, 180))

        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output




# =================================================================================================================================================


# =============================================================
#                       POLL Helper Classes
# =============================================================
class PollButton(discord.ui.Button):
    def __init__(self, index: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: "PollView" = self.view  # type: ignore
        uid = interaction.user.id

        # Toggle to single-vote: remove any previous vote by this user
        for voters in view.votes.values():
            voters.discard(uid)
        view.votes[self.index].add(uid)

        await view.update_embed()
        await interaction.response.defer()  # silent ack


class PollView(discord.ui.View):
    def __init__(self, question: str, options: list[str], author_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.question = question
        self.options = options
        self.author_id = author_id
        self.votes: dict[int, set[int]] = {i: set() for i in range(len(options))}
        self.message: discord.Message | None = None

        for i, opt in enumerate(options):
            self.add_item(PollButton(i, opt))

    async def update_embed(self):
        total = sum(len(v) for v in self.votes.values()) or 0
        lines = []
        for i, opt in enumerate(self.options):
            count = len(self.votes[i])
            pct = int(round((count / total) * 100)) if total else 0
            lines.append(f"**{opt}** — {count} vote(s) ({pct}%)")
        em = mkembed("📊 Poll Results", f"**{self.question}**\n\n" + "\n".join(lines), COLORS["INFO"])
        if self.message:
            await self.message.edit(embed=em, view=self)

    async def end_poll(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        total = sum(len(v) for v in self.votes.values())
        if total == 0:
            summary = "No votes were cast."
        else:
            summary_lines = []
            for i, opt in enumerate(self.options):
                count = len(self.votes[i])
                pct = int(round((count / total) * 100)) if total else 0
                summary_lines.append(f"**{opt}** — {count} vote(s) ({pct}%)")
            summary = "\n".join(summary_lines)

        em = mkembed(
            "📊 Final Poll Results",
            f"**{self.question}**\n\n{summary}",
            COLORS["INFO"]
        )
        if self.message:
            await self.message.edit(embed=em, view=self)
        self.stop()






# ========================== END ==========================
async def setup(bot):
    await bot.add_cog(Fun(bot))
