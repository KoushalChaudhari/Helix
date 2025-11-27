import aiohttp
import html
import random
import re, io, textwrap, contextlib
import asyncio
from datetime import datetime, timezone
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import discord
from cogs.core import mkembed, COLORS
from config import TENOR_KEY



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
    @commands.command(name="quote")
    @commands.cooldown(1, 6, commands.BucketType.user)
    async def quote(self, ctx: commands.Context, *, message_ref: str | None = None):
        """
        Create a stylized quote image from a message.
        Usage:
          - Reply to a message, then send: ;quote
          - ;quote <message_id>
          - ;quote <message_link>
        """
        msg = await self._q_resolve_message(ctx, message_ref)
        if not msg:
            return await ctx.reply(embed=mkembed("❌ Message Not Found",
                "Reply to a message or provide a valid **message ID/link**.",
                COLORS["ERROR"]))
        if msg.author.bot:
            return await ctx.reply(embed=mkembed("🚫 Not Allowed",
                "I don’t make quotes from **bot messages**.",
                COLORS["ERROR"]))

        text = (msg.content or "").strip()
        # basic limits to avoid huge canvases
        MAX_CHARS, MAX_LINES = 240, 8
        if not text:
            return await ctx.reply(embed=mkembed("⚠️ Empty Message",
                "That message has no text to quote.",
                COLORS["WARNING"]))
        if len(text) > MAX_CHARS or text.count("\n") >= MAX_LINES:
            return await ctx.reply(embed=mkembed("⚠️ Too Long",
                f"Keep it under **{MAX_CHARS} chars** or **{MAX_LINES} lines**.",
                COLORS["WARNING"]))

        # Render image (always produce a file)
        try:
            file = await self._q_render_card(ctx, msg, text)
        except Exception as e:
            # last-resort: still return a minimal image rather than an embed
            file = self._q_minimal_card(f"“{text}”\n— {msg.author.display_name}")

        em = mkembed("💬 Quote", f"[Jump to original message]({msg.jump_url})", COLORS["INFO"])
        em.set_footer(text=f'{msg.author} • {msg.created_at.strftime("%Y-%m-%d %H:%M")}')
        em.set_image(url="attachment://quote.png")
        await ctx.reply(embed=em, file=file)

# ========================== QUOTE HELPERS ==========================
    async def _q_resolve_message(self, ctx: commands.Context, ref: str | None):
        # reply
        if ctx.message.reference and ctx.message.reference.message_id:
            with contextlib.suppress(Exception):
                return await ctx.channel.fetch_message(ctx.message.reference.message_id)

        if not ref:
            return None

        ref = ref.strip().strip("<>")
        # link
        m = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", ref)
        if m:
            gid, cid, mid = map(int, m.groups())
            try:
                ch = None
                if ctx.guild and ctx.guild.id == gid:
                    ch = ctx.guild.get_channel(cid) or await ctx.guild.fetch_channel(cid)
                else:
                    ch = await self.bot.fetch_channel(cid)
                return await ch.fetch_message(mid)
            except Exception:
                return None

        # raw id (current channel)
        if re.fullmatch(r"\d{15,25}", ref):
            with contextlib.suppress(Exception):
                return await ctx.channel.fetch_message(int(ref))
        return None

    # ---------- robust font loader ----------
    def _q_font(self, size: int):
        for p in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # ---------- wrap by pixel width ----------
    def _q_wrap(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int):
        words = text.split()
        lines, cur = [], ""
        for w in words:
            test = (cur + " " + w).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    # ---------- main renderer (always produces an image) ----------
    async def _q_render_card(self, ctx: commands.Context, msg: discord.Message, text: str) -> discord.File:
        """Render quote with avatar background + translucent speech bubble."""
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        import io, contextlib

        W, H = 1280, 720
        bg = Image.new("RGB", (W, H), (16, 18, 24))
        draw = ImageDraw.Draw(bg)

        # 1️⃣ avatar background (blurred, scaled)
        with contextlib.suppress(Exception):
            av_bytes = await msg.author.display_avatar.read()
            avatar = Image.open(io.BytesIO(av_bytes)).convert("RGB").resize((W, H))
            avatar = avatar.filter(ImageFilter.GaussianBlur(20))
            bg.paste(avatar)

        # 2️⃣ speech bubble panel (≈75 % width)
        bubble_w = int(W * 0.75)
        bubble_h = int(H * 0.6)
        bx = (W - bubble_w) // 2
        by = (H - bubble_h) // 2
        bubble = (bx, by, bx + bubble_w, by + bubble_h)
        draw.rounded_rectangle(bubble, radius=40, outline=(255,255,255,30), width=3)

        # inner text margins
        margin_x = 80
        margin_y = 70
        text_area_w = bubble_w - 2 * margin_x
        start_y = by + margin_y

        # fonts
        quote_f = self._q_font(64)
        author_f = self._q_font(36)
        tag_f = self._q_font(26)

        # wrap quote
        quoted = f"“{text}”"
        lines = self._q_wrap(draw, quoted, quote_f, text_area_w)
        line_h = quote_f.getbbox("Ay")[3] - quote_f.getbbox("Ay")[1] + 8
        block_h = line_h * len(lines)
        text_y = start_y + (bubble_h - block_h) // 3

        # 3️⃣ draw quote text (centered)
        for line in lines:
            line_w = draw.textlength(line, font=quote_f)
            x = bx + (bubble_w - line_w) // 2
            draw.text((x + 2, text_y + 2), line, font=quote_f, fill=(0, 0, 0))
            draw.text((x, text_y), line, font=quote_f, fill=(240, 240, 242))
            text_y += line_h

        # 4️⃣ author line
        author_line = f"— {msg.author.display_name}"
        aw = draw.textlength(author_line, font=author_f)
        ax = bx + (bubble_w - aw) // 2
        ay = by + bubble_h - margin_y - 60
        draw.text((ax + 2, ay + 2), author_line, font=author_f, fill=(0, 0, 0))
        draw.text((ax, ay), author_line, font=author_f, fill=(220, 222, 225))

        # 5️⃣ username tag
        tag_line = f"@{getattr(msg.author, 'name', 'user')}"
        tw = draw.textlength(tag_line, font=tag_f)
        tx = bx + (bubble_w - tw) // 2
        ty = ay + 34
        draw.text((tx, ty), tag_line, font=tag_f, fill=(190, 192, 195))

        # 6️⃣ output
        buf = io.BytesIO()
        bg.save(buf, "PNG")
        buf.seek(0)
        return discord.File(buf, filename="quote.png")



    # ---------- minimal image if rendering fails ----------
    def _q_minimal_card(self, text: str) -> discord.File:
        W, H = 1280, 720
        img = Image.new("RGB", (W, H), (12, 12, 14))
        d = ImageDraw.Draw(img)
        f = self._q_font(54)
        # simple center
        bbox = d.textbbox((0, 0), text, font=f)
        x = (W - (bbox[2]-bbox[0])) // 2
        y = (H - (bbox[3]-bbox[1])) // 2
        d.text((x+3, y+3), text, font=f, fill=(0,0,0))
        d.text((x, y), text, font=f, fill=(235,235,240))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return discord.File(buf, filename="quote.png")




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
