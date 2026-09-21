import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils

CUR = config.CURRENCY_EMOJI
NAME = config.CURRENCY_NAME

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

JOBS = [
    "worked a shift as a barista", "delivered pizzas across town", "fixed a broken server",
    "streamed for a few hours", "walked the neighbours' dogs", "designed a logo for a client",
    "played music on the street", "helped out at the space station", "sold handmade crafts",
]

# (name, base value, weight)
FISH = [
    ("an old boot 🥾", 0, 20), ("a tiny minnow 🐟", 60, 30), ("a trout 🐟", 150, 25),
    ("a salmon 🍣", 300, 15), ("a golden carp ✨🐠", 900, 6), ("a legendary kraken tentacle 🐙", 3500, 1),
]
HUNT = [
    ("nothing, the animal got away 💨", 0, 20), ("a rabbit 🐇", 100, 30), ("a deer 🦌", 280, 22),
    ("a wild boar 🐗", 450, 15), ("a bear 🐻", 1000, 6), ("a baby dragon 🐉", 4000, 1),
]

CARD_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
CARD_SUITS = ["♠️", "♥️", "♦️", "♣️"]


def money(n) -> str:
    return f"{CUR} **{utils.fmt(n)}**"


# ---------------------------------------------------------------- blackjack ---
def new_deck():
    deck = [(rank, suit) for rank in CARD_RANKS for suit in CARD_SUITS]
    random.shuffle(deck)
    return deck


def card_value(rank):
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def hand_value(hand):
    total = sum(card_value(rank) for rank, _ in hand)
    aces = sum(1 for rank, _ in hand if rank == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def show_hand(hand, hide_last=False):
    if hide_last:
        return f"`{hand[0][0]}{hand[0][1]}` `??`"
    return " ".join(f"`{rank}{suit}`" for rank, suit in hand)


class BlackjackView(discord.ui.View):
    def __init__(self, bot, author, bet):
        super().__init__(timeout=90)
        self.bot = bot
        self.author = author
        self.bet = bet
        self.deck = new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.msg = None
        self.done = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    def build_embed(self, reveal=False, title="♠️ Blackjack", color=config.COLOR_MAIN, footer=None):
        embed = discord.Embed(title=title, color=color)
        embed.add_field(name=f"Your hand ({hand_value(self.player)})", value=show_hand(self.player), inline=False)
        if reveal:
            embed.add_field(name=f"Dealer ({hand_value(self.dealer)})", value=show_hand(self.dealer), inline=False)
        else:
            embed.add_field(name="Dealer (?)", value=show_hand(self.dealer, hide_last=True), inline=False)
        embed.add_field(name="Bet", value=money(self.bet))
        if footer:
            embed.set_footer(text=footer)
        return embed

    async def payout(self, outcome):
        multiplier = {"blackjack": 2.5, "win": 2, "push": 1}.get(outcome, 0)
        amount = int(self.bet * multiplier)
        if amount:
            await self.bot.db.add_balance(self.author.id, amount)
        return amount

    def result_embed(self, outcome, amount):
        titles = {
            "blackjack": ("🃏 Blackjack!", config.COLOR_OK),
            "win": ("🎉 You won!", config.COLOR_OK),
            "lose": ("💸 You lost.", config.COLOR_ERR),
            "bust": ("💥 Bust! You lost.", config.COLOR_ERR),
            "push": ("🤝 Push, your bet is returned.", config.COLOR_WARN),
        }
        title, color = titles[outcome]
        net = amount - self.bet
        return self.build_embed(reveal=True, title=title, color=color, footer=f"Net: {net:+,} tokens")

    async def end(self, interaction, outcome):
        self.done = True
        amount = await self.payout(outcome)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=self.result_embed(outcome, amount), view=self)
        self.stop()

    async def dealer_turn(self, interaction):
        while hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        player, dealer = hand_value(self.player), hand_value(self.dealer)
        if dealer > 21 or player > dealer:
            outcome = "win"
        elif player == dealer:
            outcome = "push"
        else:
            outcome = "lose"
        await self.end(interaction, outcome)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.success)
    async def hit(self, interaction, button):
        self.player.append(self.deck.pop())
        value = hand_value(self.player)
        if value > 21:
            return await self.end(interaction, "bust")
        if value == 21:
            return await self.dealer_turn(interaction)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger)
    async def stand(self, interaction, button):
        await self.dealer_turn(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.secondary)
    async def double(self, interaction, button):
        if len(self.player) != 2:
            return await interaction.response.send_message("You can only double on your first move.", ephemeral=True)
        if not await self.bot.db.take_balance(self.author.id, self.bet):
            return await interaction.response.send_message(f"You don't have enough {NAME}s to double.", ephemeral=True)
        self.bet *= 2
        self.player.append(self.deck.pop())
        if hand_value(self.player) > 21:
            return await self.end(interaction, "bust")
        await self.dealer_turn(interaction)

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        if self.msg:
            try:
                await self.msg.edit(
                    embed=self.build_embed(reveal=True, title="⌛ Timed out, you lost your bet.",
                                           color=config.COLOR_ERR),
                    view=None)
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------- cog ---
class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def take_bet(self, ctx, amount_text):
        """Parses the bet and removes it from the balance. Returns the amount, or None if invalid."""
        balance = await self.bot.db.get_balance(ctx.author.id)
        amount = utils.parse_amount(amount_text, balance)
        if amount is None or amount <= 0:
            await ctx.send(embed=utils.err("Invalid amount. Use a number, `10k`, `half` or `all`."), ephemeral=True)
            return None
        if not await self.bot.db.take_balance(ctx.author.id, amount):
            await ctx.send(embed=utils.err(f"You don't have enough {NAME}s. Balance: {money(balance)}"),
                           ephemeral=True)
            return None
        return amount

    async def loot(self, ctx, table, template):
        names = [row[0] for row in table]
        row = random.choices(table, weights=[r[2] for r in table])[0]
        value = int(row[1] * random.uniform(0.8, 1.2))
        if value:
            await self.bot.db.add_balance(ctx.author.id, value)
        balance = await self.bot.db.get_balance(ctx.author.id)
        text = template.format(item=row[0]) + (f" It's worth {money(value)}." if value else " Better luck next time!")
        await ctx.send(embed=utils.make_embed(
            description=f"{text}\nBalance: {money(balance)}", color=config.COLOR_OK if value else config.COLOR_WARN))
        return names

    # --------------------------------------------------------------- cash ---
    @commands.hybrid_command(name="cash", aliases=["balance", "bal"], description="Show your Orbit Tokens.")
    @app_commands.describe(user="The user (defaults to you)")
    async def cash(self, ctx, user: Optional[discord.User] = None):
        user = user or ctx.author
        balance = await self.bot.db.get_balance(user.id)
        embed = utils.make_embed(f"{CUR} {user.display_name}'s wallet", f"**{utils.fmt(balance)}** {NAME}s",
                                 config.COLOR_MAIN)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Your tokens are the same in every server that has Orbit.")
        await ctx.send(embed=embed)

    # ------------------------------------------------------ work / fish / hunt ---
    @commands.hybrid_command(name="work", description="Work to earn Orbit Tokens.")
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def work(self, ctx):
        amount = random.randint(150, 600)
        await self.bot.db.add_balance(ctx.author.id, amount)
        balance = await self.bot.db.get_balance(ctx.author.id)
        await ctx.send(embed=utils.make_embed(
            description=f"💼 You {random.choice(JOBS)} and earned {money(amount)}.\nBalance: {money(balance)}",
            color=config.COLOR_OK))

    @commands.hybrid_command(name="fish", description="Go fishing.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish(self, ctx):
        await self.loot(ctx, FISH, "🎣 You went fishing and caught {item}!")

    @commands.hybrid_command(name="hunt", description="Go hunting.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def hunt(self, ctx):
        await self.loot(ctx, HUNT, "🏹 You went hunting and found {item}!")

    # ----------------------------------------------------------- gambling ---
    @commands.hybrid_command(name="blackjack", aliases=["bj"], description="Play blackjack.")
    @app_commands.describe(bet="Your bet: a number, 10k, half or all")
    async def blackjack(self, ctx, bet: str):
        amount = await self.take_bet(ctx, bet)
        if amount is None:
            return
        view = BlackjackView(self.bot, ctx.author, amount)
        if hand_value(view.player) == 21:
            outcome = "push" if hand_value(view.dealer) == 21 else "blackjack"
            view.done = True
            paid = await view.payout(outcome)
            return await ctx.send(embed=view.result_embed(outcome, paid))
        view.msg = await ctx.send(embed=view.build_embed(), view=view)

    @commands.hybrid_command(name="roulette", description="Bet on the roulette: red, black, green, even, odd or a number.")
    @app_commands.describe(bet="Your bet: a number, 10k, half or all", choice="red, black, green, even, odd or 0-36")
    async def roulette(self, ctx, bet: str, choice: str):
        choice = choice.lower()
        if choice in ("red", "black", "green", "even", "odd"):
            kind, number = choice, None
        elif choice.isdigit() and 0 <= int(choice) <= 36:
            kind, number = "number", int(choice)
        else:
            return await ctx.send(embed=utils.err("Choose `red`, `black`, `green`, `even`, `odd` or a number 0-36."),
                                  ephemeral=True)
        amount = await self.take_bet(ctx, bet)
        if amount is None:
            return

        spin = random.randint(0, 36)
        color = "green" if spin == 0 else "red" if spin in RED_NUMBERS else "black"
        icon = {"green": "🟢", "red": "🔴", "black": "⚫"}[color]

        multiplier = 0
        if kind == "number" and spin == number:
            multiplier = 36
        elif kind == "green" and spin == 0:
            multiplier = 14
        elif kind in ("red", "black") and color == kind:
            multiplier = 2
        elif kind == "even" and spin != 0 and spin % 2 == 0:
            multiplier = 2
        elif kind == "odd" and spin % 2 == 1:
            multiplier = 2

        payout = amount * multiplier
        if payout:
            await self.bot.db.add_balance(ctx.author.id, payout)
        balance = await self.bot.db.get_balance(ctx.author.id)
        if payout:
            title, result, embed_color = "🎡 Roulette", f"You won {money(payout - amount)}! 🎉", config.COLOR_OK
        else:
            title, result, embed_color = "🎡 Roulette", f"You lost {money(amount)}. 💸", config.COLOR_ERR
        await ctx.send(embed=utils.make_embed(
            title, f"The ball landed on {icon} **{spin}**.\n{result}\nBalance: {money(balance)}", embed_color))

    @commands.hybrid_command(name="coinflip", aliases=["cf"], description="Flip a coin and bet on heads or tails.")
    @app_commands.describe(bet="Your bet: a number, 10k, half or all", side="heads or tails")
    @app_commands.choices(side=[app_commands.Choice(name="heads", value="heads"),
                                app_commands.Choice(name="tails", value="tails")])
    async def coinflip(self, ctx, bet: str, side: str):
        side = side.lower()
        side = {"h": "heads", "head": "heads", "t": "tails", "tail": "tails"}.get(side, side)
        if side not in ("heads", "tails"):
            return await ctx.send(embed=utils.err("Choose `heads` or `tails`."), ephemeral=True)
        amount = await self.take_bet(ctx, bet)
        if amount is None:
            return
        result = random.choice(["heads", "tails"])
        won = result == side
        if won:
            await self.bot.db.add_balance(ctx.author.id, amount * 2)
        balance = await self.bot.db.get_balance(ctx.author.id)
        text = f"You won {money(amount)}! 🎉" if won else f"You lost {money(amount)}. 💸"
        await ctx.send(embed=utils.make_embed(
            "🪙 Coinflip", f"The coin landed on **{result}**.\n{text}\nBalance: {money(balance)}",
            config.COLOR_OK if won else config.COLOR_ERR))

    # ------------------------------------------------------ give / add / remove ---
    @commands.hybrid_command(name="give", description="Give some of your Orbit Tokens to another user.")
    @app_commands.describe(user="Who gets the tokens", amount="A number, 10k, half or all")
    async def give(self, ctx, user: discord.User, amount: str):
        if user.id == ctx.author.id or user.bot:
            return await ctx.send(embed=utils.err("You can't give tokens to that user."), ephemeral=True)
        balance = await self.bot.db.get_balance(ctx.author.id)
        value = utils.parse_amount(amount, balance)
        if value is None or value <= 0:
            return await ctx.send(embed=utils.err("Invalid amount. Use a number, `10k`, `half` or `all`."),
                                  ephemeral=True)
        if not await self.bot.db.take_balance(ctx.author.id, value):
            return await ctx.send(embed=utils.err(f"You don't have enough {NAME}s. Balance: {money(balance)}"),
                                  ephemeral=True)
        await self.bot.db.add_balance(user.id, value)
        await ctx.send(embed=utils.ok(f"You gave {money(value)} to **{user.display_name}**."))

    @commands.hybrid_command(name="add", description="(Owner only) Add Orbit Tokens to a user.")
    @app_commands.describe(user="The user", amount="How many tokens")
    @utils.owner_only()
    async def add(self, ctx, user: discord.User, amount: commands.Range[int, 1, 1_000_000_000_000]):
        await self.bot.db.add_balance(user.id, amount)
        balance = await self.bot.db.get_balance(user.id)
        await ctx.send(embed=utils.ok(f"Added {money(amount)} to **{user.display_name}**. New balance: {money(balance)}"))

    @commands.hybrid_command(name="remove", description="(Owner only) Remove Orbit Tokens from a user.")
    @app_commands.describe(user="The user", amount="How many tokens")
    @utils.owner_only()
    async def remove(self, ctx, user: discord.User, amount: commands.Range[int, 1, 1_000_000_000_000]):
        balance = await self.bot.db.get_balance(user.id)
        taken = min(amount, balance)
        if taken:
            await self.bot.db.take_balance(user.id, taken)
        await ctx.send(embed=utils.ok(
            f"Removed {money(taken)} from **{user.display_name}**. New balance: {money(balance - taken)}"))


async def setup(bot):
    await bot.add_cog(Economy(bot))
