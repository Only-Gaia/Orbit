import datetime
import hashlib
import random
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
import utils

EIGHT_BALL = [
    "It is certain. ✅", "Without a doubt. ✅", "Yes, definitely. ✅", "You may rely on it. ✅",
    "As I see it, yes. ✅", "Most likely. ✅", "Outlook good. ✅", "Signs point to yes. ✅",
    "Reply hazy, try again. 🔮", "Ask again later. 🔮", "Better not tell you now. 🔮",
    "Cannot predict now. 🔮", "Concentrate and ask again. 🔮",
    "Don't count on it. ❌", "My reply is no. ❌", "My sources say no. ❌",
    "Outlook not so good. ❌", "Very doubtful. ❌", "Absolutely not. ❌", "No way. ❌",
]


class MarryView(discord.ui.View):
    def __init__(self, cog, proposer, target):
        super().__init__(timeout=60)
        self.cog = cog
        self.proposer = proposer
        self.target = target
        self.msg = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This proposal isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", emoji="💍", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if await self.cog.spouse(self.proposer.id) or await self.cog.spouse(self.target.id):
            return await interaction.response.send_message("One of you is already married!", ephemeral=True)
        a, b = sorted((self.proposer.id, self.target.id))
        await self.cog.bot.db.execute(
            "INSERT INTO marriages (user1, user2, married_at) VALUES (?, ?, ?)", (a, b, int(time.time())))
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=utils.make_embed(
                "💒 Just married!", f"{self.proposer.mention} and {self.target.mention} are now married. 💖",
                config.COLOR_OK),
            view=self)
        self.stop()

    @discord.ui.button(label="Decline", emoji="💔", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=utils.make_embed(
                "💔 Proposal declined", f"{self.target.mention} said no to {self.proposer.mention}.",
                config.COLOR_ERR),
            view=self)
        self.stop()

    async def on_timeout(self):
        if self.msg:
            try:
                await self.msg.edit(
                    embed=utils.make_embed("⌛ No answer", "The proposal expired.", config.COLOR_WARN), view=None)
            except discord.HTTPException:
                pass


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def fetch_gif(self, endpoint):
        try:
            async with self.session.get(f"https://nekos.best/api/v2/{endpoint}",
                                        timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                return data["results"][0]["url"]
        except Exception:
            return None

    async def spouse(self, user_id):
        row = await self.bot.db.fetchone(
            "SELECT user1, user2 FROM marriages WHERE user1 = ? OR user2 = ?", (user_id, user_id))
        if row is None:
            return None
        return row["user2"] if row["user1"] == user_id else row["user1"]

    async def action(self, ctx, member, text, endpoint, color):
        await ctx.defer()
        gif = await self.fetch_gif(endpoint)
        embed = discord.Embed(description=text.format(a=ctx.author.mention, b=member.mention), color=color)
        if gif:
            embed.set_image(url=gif)
        await ctx.send(embed=embed)

    @staticmethod
    def daily_random(user_id, salt):
        seed = f"{user_id}-{datetime.date.today().isoformat()}-{salt}"
        return random.Random(seed)

    # ------------------------------------------------------------- ship ---
    @commands.hybrid_command(name="ship", description="Calculate the love compatibility between two users.")
    @app_commands.describe(user1="First user", user2="Second user (defaults to you)")
    async def ship(self, ctx, user1: discord.Member, user2: Optional[discord.Member] = None):
        if user2 is None:
            user1, user2 = ctx.author, user1
        a, b = sorted((user1.id, user2.id))
        percent = int(hashlib.md5(f"{a}:{b}".encode()).hexdigest(), 16) % 101
        filled = percent // 10
        bar = "❤️" * filled + "🖤" * (10 - filled)
        if percent >= 90:
            verdict = "A match made in heaven! 💞"
        elif percent >= 70:
            verdict = "Looking really good! 😍"
        elif percent >= 50:
            verdict = "There's something there... 😉"
        elif percent >= 30:
            verdict = "Maybe as friends. 🙂"
        else:
            verdict = "Not this time... 😅"
        n1, n2 = user1.display_name, user2.display_name
        ship_name = n1[: max(1, len(n1) // 2)] + n2[len(n2) // 2:]
        await ctx.send(embed=utils.make_embed(
            f"💘 {ship_name}", f"{user1.mention} + {user2.mention}\n\n{bar}\n**{percent}%** — {verdict}",
            0xEB459E))

    # ----------------------------------------------- roleplay-style actions ---
    @commands.hybrid_command(name="slap", description="Slap someone (playfully).")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def slap(self, ctx, member: discord.Member):
        await self.action(ctx, member, "👋 {a} slaps {b}!", "slap", 0xE67E22)

    @commands.hybrid_command(name="kiss", description="Kiss someone.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def kiss(self, ctx, member: discord.Member):
        await self.action(ctx, member, "💋 {a} kisses {b}!", "kiss", 0xEB459E)

    @commands.hybrid_command(name="fakekick", description="Pretend to kick someone (it's a joke, nobody is kicked).")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fakekick(self, ctx, member: discord.Member):
        await self.action(ctx, member, "🦵 {a} kicks {b} out of the room! (just kidding, nobody was harmed)",
                          "kick", 0xE67E22)

    @commands.hybrid_command(name="kill", description="Defeat someone in a (very fake) epic battle.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def kill(self, ctx, member: discord.Member):
        await self.action(ctx, member, "💀 {a} defeats {b} in an epic (totally fake) battle!", "shoot", 0x992D22)

    @commands.hybrid_command(name="hug", description="Hug someone.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def hug(self, ctx, member: discord.Member):
        await self.action(ctx, member, "🤗 {a} hugs {b}!", "hug", 0x57F287)

    # ------------------------------------------------------ meters and 8ball ---
    @commands.hybrid_command(name="aura", description="Calculate how much aura someone has today.")
    @app_commands.describe(member="The member (defaults to you)")
    async def aura(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        points = self.daily_random(member.id, "aura").randint(-1000, 10000)
        if points >= 8000:
            tier = "Legendary aura 👑"
        elif points >= 4000:
            tier = "Strong aura 🔥"
        elif points >= 1000:
            tier = "Decent aura ✨"
        elif points >= 0:
            tier = "Low aura 🙂"
        else:
            tier = "Negative aura... 💀"
        await ctx.send(embed=utils.make_embed(
            "✨ Aura reading", f"**{member.display_name}** has **{points:,}** aura points today.\n{tier}",
            config.COLOR_MAIN))

    @commands.hybrid_command(name="gay", description="A just-for-fun rainbow meter.")
    @app_commands.describe(member="The member (defaults to you)")
    async def gay(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        percent = self.daily_random(member.id, "rainbow").randint(0, 100)
        await ctx.send(embed=utils.make_embed(
            "🌈 Rainbow meter", f"**{member.display_name}** is **{percent}%** gay today.\n*(just for fun!)*",
            0xEB459E))

    @commands.hybrid_command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="Your yes/no question")
    async def eightball(self, ctx, *, question: str):
        await ctx.send(embed=utils.make_embed(
            "🎱 Magic 8-ball", f"**Question:** {utils.clip(question, 300)}\n**Answer:** {random.choice(EIGHT_BALL)}",
            config.COLOR_MAIN))

    # ------------------------------------------------------------------ say ---
    @commands.hybrid_command(name="say", description="Make the bot say something.")
    @app_commands.describe(text="What the bot should say")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def say(self, ctx, *, text: str):
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        await ctx.channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        if ctx.interaction is not None:
            await ctx.send(embed=utils.ok("Sent!"), ephemeral=True)

    # ------------------------------------------------------ marry / divorce ---
    @commands.hybrid_command(name="marry", description="Propose to someone.")
    @app_commands.describe(member="Who you want to marry")
    @commands.guild_only()
    async def marry(self, ctx, member: discord.Member):
        if member.id == ctx.author.id or member.bot:
            return await ctx.send(embed=utils.err("You can't marry that user."), ephemeral=True)
        if await self.spouse(ctx.author.id):
            return await ctx.send(embed=utils.err("You're already married! Use `divorce` first."), ephemeral=True)
        if await self.spouse(member.id):
            return await ctx.send(embed=utils.err(f"**{member.display_name}** is already married."), ephemeral=True)
        view = MarryView(self, ctx.author, member)
        view.msg = await ctx.send(
            content=member.mention,
            embed=utils.make_embed("💍 Marriage proposal",
                                   f"{ctx.author.mention} wants to marry {member.mention}!\nDo you accept?",
                                   0xEB459E),
            view=view)

    @commands.hybrid_command(name="divorce", description="End your marriage.")
    async def divorce(self, ctx):
        partner = await self.spouse(ctx.author.id)
        if partner is None:
            return await ctx.send(embed=utils.err("You're not married."), ephemeral=True)
        await self.bot.db.execute("DELETE FROM marriages WHERE user1 = ? OR user2 = ?", (ctx.author.id, ctx.author.id))
        await ctx.send(embed=utils.make_embed(
            "💔 Divorced", f"{ctx.author.mention} and <@{partner}> are no longer married.", config.COLOR_ERR))

    # -------------------------------------------------------------- fakeban ---
    @commands.hybrid_command(name="fakeban", description="Pretend to ban someone (it's a joke, nobody is banned).")
    @app_commands.describe(member="Who to fake-ban", reason="Why")
    @commands.guild_only()
    async def fakeban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        embed = utils.make_embed(
            "🔨 Member banned", f"**{member}** has been banned from the server.\n**Reason:** {reason}",
            config.COLOR_ERR)
        embed.set_footer(text="Just kidding, this is a fake ban! 😄")
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Games(bot))
