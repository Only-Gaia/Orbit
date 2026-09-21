import asyncio
import io
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

import config
import utils


def xp_needed(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    return 5 * level ** 2 + 50 * level + 100


def _font(size, bold=False):
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def render_card(avatar_bytes, name, level, rank, messages, xp, needed):
    """Draws the rank card (runs in a thread, it's CPU work)."""
    width, height = 830, 200
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, width - 1, height - 1), radius=28, fill=(28, 28, 40, 255))

    size = 132
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    d.ellipse((30, 30, 170, 170), outline=(124, 108, 240, 255), width=4)
    img.paste(avatar, (34, 34), mask)

    name_font = _font(32, True)
    while len(name) > 1 and d.textlength(name, font=name_font) > 330:
        name = name[:-1]
    d.text((200, 40), name, font=name_font, fill=(255, 255, 255, 255))
    d.text((200, 88), f"{messages:,} messages", font=_font(20), fill=(160, 160, 175, 255))

    d.text((560, 34), "LEVEL", font=_font(17, True), fill=(160, 160, 175, 255))
    d.text((560, 56), str(level), font=_font(38, True), fill=(155, 89, 255, 255))
    d.text((690, 34), "RANK", font=_font(17, True), fill=(160, 160, 175, 255))
    d.text((690, 56), f"#{rank}", font=_font(38, True), fill=(255, 255, 255, 255))

    x0, y0, x1, y1 = 200, 138, 790, 164
    d.rounded_rectangle((x0, y0, x1, y1), radius=13, fill=(45, 45, 60, 255))
    pct = min(1.0, xp / needed) if needed else 0
    fill_width = max(26, int((x1 - x0) * pct))
    d.rounded_rectangle((x0, y0, x0 + fill_width, y1), radius=13, fill=(155, 89, 255, 255))
    label = f"{int(pct * 100)}%"
    d.text((x1 - d.textlength(label, font=_font(17, True)), 170), label, font=_font(17, True),
           fill=(155, 89, 255, 255))

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer


class Levels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = {}

    # ------------------------------------------------------------- xp ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        gid, uid = message.guild.id, message.author.id
        now = time.time()
        gain = 0
        if now - self.cooldowns.get((gid, uid), 0) >= 60:
            self.cooldowns[(gid, uid)] = now
            gain = random.randint(15, 25)

        db = self.bot.db
        await db.execute("INSERT OR IGNORE INTO levels (guild_id, user_id) VALUES (?, ?)", (gid, uid))
        await db.execute(
            "UPDATE levels SET messages = messages + 1, xp = xp + ? WHERE guild_id = ? AND user_id = ?",
            (gain, gid, uid))
        if not gain:
            return

        row = await db.fetchone("SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?", (gid, uid))
        xp, level, leveled = row["xp"], row["level"], False
        while xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            leveled = True
        if leveled:
            await db.execute("UPDATE levels SET xp = ?, level = ? WHERE guild_id = ? AND user_id = ?",
                             (xp, level, gid, uid))
            try:
                await message.channel.send(embed=utils.make_embed(
                    description=f"🎉 {message.author.mention} reached **level {level}**!", color=config.COLOR_MAIN))
            except discord.HTTPException:
                pass

    async def stats(self, guild_id, user_id):
        db = self.bot.db
        row = await db.fetchone(
            "SELECT xp, level, messages FROM levels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        if row is None:
            total = (await db.fetchone("SELECT COUNT(*) AS c FROM levels WHERE guild_id = ?", (guild_id,)))["c"]
            return 0, 0, 0, total + 1
        rank_row = await db.fetchone(
            "SELECT COUNT(*) + 1 AS r FROM levels WHERE guild_id = ? AND (level > ? OR (level = ? AND xp > ?))",
            (guild_id, row["level"], row["level"], row["xp"]))
        return row["xp"], row["level"], row["messages"], rank_row["r"]

    # ----------------------------------------------------------- commands ---
    @commands.hybrid_command(name="level", description="Show the level of a member.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def level(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        xp, level, messages, rank = await self.stats(ctx.guild.id, member.id)
        embed = utils.make_embed(f"Level of {member.display_name}", color=config.COLOR_MAIN)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="⭐ Level", value=f"`{level}`")
        embed.add_field(name="🏆 Rank", value=f"`#{rank}`")
        embed.add_field(name="✨ XP", value=f"`{xp:,} / {xp_needed(level):,}`")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"],
                             description="Top 10 members with the highest level.")
    @commands.guild_only()
    async def leaderboard(self, ctx):
        rows = await self.bot.db.fetchall(
            "SELECT user_id, level, xp FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10",
            (ctx.guild.id,))
        if not rows:
            return await ctx.send(embed=utils.make_embed(description="Nobody has any XP yet."))
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(f"{prefix} <@{r['user_id']}> — level **{r['level']}** ({r['xp']:,} xp)")
        await ctx.send(embed=utils.make_embed("🏆 Level leaderboard", "\n".join(lines)))

    @commands.hybrid_command(name="rank", description="Show your rank card.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def rank(self, ctx, member: Optional[discord.Member] = None):
        await ctx.defer()
        member = member or ctx.author
        xp, level, messages, rank = await self.stats(ctx.guild.id, member.id)
        avatar = await member.display_avatar.replace(size=256, format="png").read()
        card = await asyncio.to_thread(
            render_card, avatar, member.display_name, level, rank, messages, xp, xp_needed(level))
        await ctx.send(file=discord.File(card, filename="rank.png"))

    @commands.hybrid_command(name="messages", aliases=["message", "msgs"],
                             description="Show how many messages a member has sent.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def messages(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        _, _, count, _ = await self.stats(ctx.guild.id, member.id)
        await ctx.send(embed=utils.make_embed(
            description=f"💬 **{member.display_name}** has sent **{count:,}** messages in this server."))

    # ------------------------------------------------------------ resets ---
    @commands.hybrid_group(name="reset", invoke_without_command=True, description="Reset invites or message counters.")
    @commands.guild_only()
    async def reset(self, ctx):
        await ctx.send(embed=utils.err(
            f"Usage: `{config.PREFIX}reset invites <member>`, `{config.PREFIX}reset allinvites`, "
            f"`{config.PREFIX}reset messages <member>`, `{config.PREFIX}reset allmessages`"), ephemeral=True)

    @reset.command(name="invites", description="Reset the invites of a member.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def reset_invites(self, ctx, member: discord.Member):
        cursor = await self.bot.db.execute(
            "DELETE FROM invite_joins WHERE guild_id = ? AND inviter_id = ?", (ctx.guild.id, member.id))
        await ctx.send(embed=utils.ok(f"Reset the invites of **{member.display_name}** ({cursor.rowcount} removed)."))

    @reset.command(name="allinvites", description="Reset the invites of everyone.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def reset_all_invites(self, ctx):
        if not await utils.confirm(ctx, "This will erase the invites of **everyone** in this server. Continue?"):
            return await ctx.send(embed=utils.make_embed(description="Cancelled."), ephemeral=True)
        cursor = await self.bot.db.execute("DELETE FROM invite_joins WHERE guild_id = ?", (ctx.guild.id,))
        await ctx.send(embed=utils.ok(f"Reset all invites ({cursor.rowcount} entries removed)."))

    @reset.command(name="messages", description="Reset the message counter of a member.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def reset_messages(self, ctx, member: discord.Member):
        await self.bot.db.execute(
            "UPDATE levels SET messages = 0 WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        await ctx.send(embed=utils.ok(f"Reset the message counter of **{member.display_name}**."))

    @reset.command(name="allmessages", description="Reset the message counter of everyone.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def reset_all_messages(self, ctx):
        if not await utils.confirm(ctx, "This will reset the message counter of **everyone** in this server. Continue?"):
            return await ctx.send(embed=utils.make_embed(description="Cancelled."), ephemeral=True)
        await self.bot.db.execute("UPDATE levels SET messages = 0 WHERE guild_id = ?", (ctx.guild.id,))
        await ctx.send(embed=utils.ok("Reset all message counters."))


async def setup(bot):
    await bot.add_cog(Levels(bot))
