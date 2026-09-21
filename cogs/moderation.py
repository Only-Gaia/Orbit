import datetime
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils

NO_REASON = "No reason provided"


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def dm_user(self, user, action, guild, reason):
        try:
            await user.send(embed=utils.make_embed(
                f"You were {action} {guild.name}", f"**Reason:** {reason}", config.COLOR_ERR))
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def fail(self, ctx, text):
        await ctx.send(embed=utils.err(text), ephemeral=True)

    # ------------------------------------------------------------- ban ---
    @commands.hybrid_command(name="ban", description="Ban a user from the server.")
    @app_commands.describe(user="The user to ban", reason="Why they are being banned")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, user: discord.User, *, reason: str = NO_REASON):
        member = ctx.guild.get_member(user.id)
        if member is not None:
            problem = utils.hierarchy_error(ctx.author, member)
        elif user.id == ctx.author.id:
            problem = "You can't ban yourself."
        else:
            problem = None
        if problem:
            return await self.fail(ctx, problem)
        await self.dm_user(user, "banned from", ctx.guild, reason)
        try:
            await ctx.guild.ban(user, reason=utils.audit_reason(ctx.author, reason), delete_message_seconds=0)
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't ban that user: {e}")
        await ctx.send(embed=utils.ok(f"**{user}** has been banned.\n**Reason:** {reason}"))

    @commands.hybrid_command(name="unban", description="Unban a user (use their ID).")
    @app_commands.describe(user="The user ID to unban", reason="Why they are being unbanned")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: discord.User, *, reason: str = NO_REASON):
        try:
            await ctx.guild.unban(user, reason=utils.audit_reason(ctx.author, reason))
        except discord.NotFound:
            return await self.fail(ctx, "That user isn't banned.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't unban that user: {e}")
        await ctx.send(embed=utils.ok(f"**{user}** has been unbanned."))

    # ------------------------------------------------------------ kick ---
    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="The member to kick", reason="Why they are being kicked")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason: str = NO_REASON):
        problem = utils.hierarchy_error(ctx.author, member)
        if problem:
            return await self.fail(ctx, problem)
        await self.dm_user(member, "kicked from", ctx.guild, reason)
        try:
            await member.kick(reason=utils.audit_reason(ctx.author, reason))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't kick that member: {e}")
        await ctx.send(embed=utils.ok(f"**{member}** has been kicked.\n**Reason:** {reason}"))

    # ------------------------------------------------------------ warns ---
    @commands.hybrid_command(name="warn", description="Warn a member.")
    @app_commands.describe(member="The member to warn", reason="Why they are being warned")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member, *, reason: str = NO_REASON):
        problem = utils.hierarchy_error(ctx.author, member, check_bot=False)
        if problem:
            return await self.fail(ctx, problem)
        cursor = await self.bot.db.execute(
            "INSERT INTO warns (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (ctx.guild.id, member.id, ctx.author.id, reason, int(time.time())),
        )
        row = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS c FROM warns WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
        await self.dm_user(member, "warned in", ctx.guild, reason)
        await ctx.send(embed=utils.ok(
            f"**{member}** has been warned (warn `#{cursor.lastrowid}`, total: {row['c']}).\n**Reason:** {reason}"))

    @commands.hybrid_command(name="unwarn", description="Remove a warn (latest one by default, or a warn ID, or 'all').")
    @app_commands.describe(member="The member", warn_id="A warn ID, or 'all'")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def unwarn(self, ctx, member: discord.Member, warn_id: Optional[str] = None):
        db = self.bot.db
        if warn_id and warn_id.lower() == "all":
            cursor = await db.execute(
                "DELETE FROM warns WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, member.id))
            return await ctx.send(embed=utils.ok(f"Removed **{cursor.rowcount}** warn(s) from **{member}**."))
        if warn_id:
            try:
                target_id = int(warn_id.lstrip("#"))
            except ValueError:
                return await self.fail(ctx, "The warn ID must be a number (or `all`).")
        else:
            row = await db.fetchone(
                "SELECT id FROM warns WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                (ctx.guild.id, member.id))
            if row is None:
                return await self.fail(ctx, f"**{member}** has no warns.")
            target_id = row["id"]
        cursor = await db.execute(
            "DELETE FROM warns WHERE id = ? AND guild_id = ? AND user_id = ?", (target_id, ctx.guild.id, member.id))
        if cursor.rowcount == 0:
            return await self.fail(ctx, "I couldn't find that warn for this member.")
        await ctx.send(embed=utils.ok(f"Removed warn `#{target_id}` from **{member}**."))

    @commands.hybrid_command(name="warns", aliases=["showwarn", "showwarns"], description="Show the warns of a member.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def warns(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        if member.id != ctx.author.id and not ctx.author.guild_permissions.moderate_members:
            return await self.fail(ctx, "You can only see your own warns.")
        rows = await self.bot.db.fetchall(
            "SELECT id, moderator_id, reason, created_at FROM warns WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id DESC LIMIT 25", (ctx.guild.id, member.id))
        if not rows:
            return await ctx.send(embed=utils.make_embed(description=f"**{member}** has no warns. ✨"))
        lines = [f"`#{r['id']}` <t:{r['created_at']}:d> by <@{r['moderator_id']}> — {utils.clip(r['reason'], 150)}"
                 for r in rows]
        embed = utils.make_embed(f"Warns for {member}", "\n".join(lines), config.COLOR_WARN)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------ nick ---
    @commands.hybrid_group(name="nick", invoke_without_command=True, description="Change nicknames.")
    @commands.guild_only()
    async def nick(self, ctx):
        await self.fail(ctx, f"Usage: `{config.PREFIX}nick set <member> <nickname>` or `{config.PREFIX}nick reset <member>`")

    @nick.command(name="set", description="Rename a member.")
    @app_commands.describe(member="The member to rename", nickname="The new nickname")
    @commands.guild_only()
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick_set(self, ctx, member: discord.Member, *, nickname: str):
        problem = utils.hierarchy_error(ctx.author, member, allow_self=True)
        if problem:
            return await self.fail(ctx, problem)
        if len(nickname) > 32:
            return await self.fail(ctx, "Nicknames can be at most 32 characters.")
        try:
            await member.edit(nick=nickname, reason=utils.audit_reason(ctx.author, "nick set"))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't change that nickname: {e}")
        await ctx.send(embed=utils.ok(f"**{member.name}** is now called **{nickname}**."))

    @nick.command(name="reset", description="Remove a member's nickname.")
    @commands.guild_only()
    @commands.has_permissions(manage_nicknames=True)
    @commands.bot_has_permissions(manage_nicknames=True)
    async def nick_reset(self, ctx, member: discord.Member):
        problem = utils.hierarchy_error(ctx.author, member, allow_self=True)
        if problem:
            return await self.fail(ctx, problem)
        try:
            await member.edit(nick=None, reason=utils.audit_reason(ctx.author, "nick reset"))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't reset that nickname: {e}")
        await ctx.send(embed=utils.ok(f"Reset the nickname of **{member.name}**."))

    # ------------------------------------------------------- pex / depex ---
    @commands.hybrid_command(name="pex", description="Give a role to a member.")
    @app_commands.describe(member="The member", role="The role to give")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def pex(self, ctx, member: discord.Member, role: discord.Role):
        problem = utils.role_error(ctx.author, member, role)
        if problem:
            return await self.fail(ctx, problem)
        if role in member.roles:
            return await self.fail(ctx, f"**{member.name}** already has {role.mention}.")
        try:
            await member.add_roles(role, reason=utils.audit_reason(ctx.author, "pex"))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't give that role: {e}")
        await ctx.send(embed=utils.ok(f"Gave {role.mention} to **{member.name}**."))

    @commands.hybrid_command(name="depex", description="Remove a role from a member.")
    @app_commands.describe(member="The member", role="The role to remove")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def depex(self, ctx, member: discord.Member, role: discord.Role):
        problem = utils.role_error(ctx.author, member, role)
        if problem:
            return await self.fail(ctx, problem)
        if role not in member.roles:
            return await self.fail(ctx, f"**{member.name}** doesn't have {role.mention}.")
        try:
            await member.remove_roles(role, reason=utils.audit_reason(ctx.author, "depex"))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't remove that role: {e}")
        await ctx.send(embed=utils.ok(f"Removed {role.mention} from **{member.name}**."))

    # ------------------------------------------------------ mute / unmute ---
    @commands.hybrid_command(name="mute", description="Timeout a member (e.g. 10m, 2h, 1d).")
    @app_commands.describe(member="The member to mute", duration="For example 10m, 2h, 1d", reason="Why")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = NO_REASON):
        seconds = utils.parse_duration(duration)
        if not seconds:
            return await self.fail(ctx, "Invalid duration. Examples: `10m`, `2h`, `1d`.")
        if seconds > 28 * 86400:
            return await self.fail(ctx, "The maximum mute duration is 28 days.")
        problem = utils.hierarchy_error(ctx.author, member)
        if problem:
            return await self.fail(ctx, problem)
        try:
            await member.timeout(datetime.timedelta(seconds=seconds), reason=utils.audit_reason(ctx.author, reason))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't mute that member: {e}")
        await ctx.send(embed=utils.ok(
            f"**{member}** has been muted for **{utils.format_duration(seconds)}**.\n**Reason:** {reason}"))

    @commands.hybrid_command(name="unmute", description="Remove a member's timeout.")
    @app_commands.describe(member="The member to unmute")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx, member: discord.Member, *, reason: str = NO_REASON):
        if not member.is_timed_out():
            return await self.fail(ctx, f"**{member}** isn't muted.")
        try:
            await member.timeout(None, reason=utils.audit_reason(ctx.author, reason))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't unmute that member: {e}")
        await ctx.send(embed=utils.ok(f"**{member}** has been unmuted."))

    # ------------------------------------------------------------ purge ---
    @commands.hybrid_command(name="purge", description="Delete recent messages (optionally only from one member).")
    @app_commands.describe(amount="How many messages to check (1-1000)", member="Only delete this member's messages")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, ctx, amount: commands.Range[int, 1, 1000], member: Optional[discord.Member] = None):
        await ctx.defer(ephemeral=True)
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        check = (lambda m: m.author.id == member.id) if member else None
        try:
            deleted = await ctx.channel.purge(limit=amount, check=check, reason=utils.audit_reason(ctx.author, "purge"))
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't delete messages: {e}")
        await ctx.send(embed=utils.ok(f"Deleted **{len(deleted)}** message(s)."), delete_after=5)

    # --------------------------------------------- slowmode / lock / unlock ---
    @commands.hybrid_command(name="slowmode", description="Set the slowmode of a channel (0 to disable).")
    @app_commands.describe(seconds="Seconds between messages (0-21600)", channel="Defaults to this channel")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx, seconds: commands.Range[int, 0, 21600], channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        await channel.edit(slowmode_delay=seconds, reason=utils.audit_reason(ctx.author, "slowmode"))
        if seconds == 0:
            await ctx.send(embed=utils.ok(f"Slowmode disabled in {channel.mention}."))
        else:
            await ctx.send(embed=utils.ok(f"Slowmode in {channel.mention} set to **{seconds}s**."))

    @commands.hybrid_command(name="lock", description="Stop @everyone from sending messages in a channel.")
    @app_commands.describe(channel="Defaults to this channel")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx, channel: Optional[discord.TextChannel] = None, *, reason: str = NO_REASON):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                      reason=utils.audit_reason(ctx.author, reason))
        await ctx.send(embed=utils.ok(f"🔒 {channel.mention} has been locked."))

    @commands.hybrid_command(name="unlock", description="Let @everyone send messages in a channel again.")
    @app_commands.describe(channel="Defaults to this channel")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx, channel: Optional[discord.TextChannel] = None, *, reason: str = NO_REASON):
        channel = channel or ctx.channel
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                      reason=utils.audit_reason(ctx.author, reason))
        await ctx.send(embed=utils.ok(f"🔓 {channel.mention} has been unlocked."))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
