from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils


def snapshot(invites):
    """{code: (uses, inviter_id, max_uses)}"""
    return {i.code: (i.uses or 0, i.inviter.id if i.inviter else None, i.max_uses or 0) for i in invites}


class Invites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}

    async def cache_guild(self, guild):
        try:
            self.cache[guild.id] = snapshot(await guild.invites())
        except (discord.Forbidden, discord.HTTPException):
            self.cache[guild.id] = {}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self.cache_guild(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.cache_guild(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild:
            self.cache.setdefault(invite.guild.id, {})[invite.code] = (
                invite.uses or 0, invite.inviter.id if invite.inviter else None, invite.max_uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if invite.guild:
            self.cache.get(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if await self.bot.db.is_blacklisted(member.id):
            return
        guild = member.guild
        old = self.cache.get(guild.id, {})
        inviter_id, code = None, None
        try:
            new = snapshot(await guild.invites())
        except (discord.Forbidden, discord.HTTPException):
            new = None

        if new is not None:
            for c, (uses, owner_id, _) in new.items():
                if uses > old.get(c, (0, None, 0))[0]:
                    inviter_id, code = owner_id, c
                    break
            if code is None:
                # A limited invite disappears when its last use is taken.
                vanished = [c for c, (uses, _, mx) in old.items() if c not in new and mx and uses + 1 >= mx]
                if len(vanished) == 1:
                    code = vanished[0]
                    inviter_id = old[code][1]
            self.cache[guild.id] = new

        if inviter_id and inviter_id != member.id:
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO invite_joins (guild_id, user_id, inviter_id, code, has_left) "
                "VALUES (?, ?, ?, ?, 0)", (guild.id, member.id, inviter_id, code))
        # The logs cog listens to this custom event.
        self.bot.dispatch("orbit_member_join", member, inviter_id, code)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await self.bot.db.execute(
            "UPDATE invite_joins SET has_left = 1 WHERE guild_id = ? AND user_id = ?", (member.guild.id, member.id))

    # ----------------------------------------------------------- commands ---
    @commands.hybrid_command(name="invites", description="Show how many people a member has invited.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def invites(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        row = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN has_left = 0 THEN 1 ELSE 0 END), 0) AS active "
            "FROM invite_joins WHERE guild_id = ? AND inviter_id = ?", (ctx.guild.id, member.id))
        total, active = row["total"], row["active"]
        embed = utils.make_embed(f"Invites of {member.display_name}", color=config.COLOR_MAIN)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="✅ Active", value=f"`{active}`")
        embed.add_field(name="🚪 Left", value=f"`{total - active}`")
        embed.add_field(name="📨 Total joins", value=f"`{total}`")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="inviteboard", aliases=["invleaderboard", "topinvites"],
                             description="Top 10 members with the most invites.")
    @commands.guild_only()
    async def inviteboard(self, ctx):
        rows = await self.bot.db.fetchall(
            "SELECT inviter_id, SUM(CASE WHEN has_left = 0 THEN 1 ELSE 0 END) AS active, COUNT(*) AS total "
            "FROM invite_joins WHERE guild_id = ? GROUP BY inviter_id ORDER BY active DESC, total DESC LIMIT 10",
            (ctx.guild.id,))
        if not rows:
            return await ctx.send(embed=utils.make_embed(description="Nobody has invited anyone yet."))
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            prefix = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(f"{prefix} <@{r['inviter_id']}> — **{r['active']}** invites ({r['total']} total)")
        await ctx.send(embed=utils.make_embed("📨 Invite leaderboard", "\n".join(lines)))


async def setup(bot):
    await bot.add_cog(Invites(bot))
