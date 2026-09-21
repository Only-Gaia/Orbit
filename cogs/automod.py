import asyncio
import collections
import datetime
import time
from typing import Literal, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils


class Automod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent = {}        # (guild, user) -> deque[(time, message)]
        self.punished = {}      # (guild, user) -> time of last punishment
        self.nuke = {}          # (guild, executor) -> deque[time]
        self.joins = {}         # guild -> deque[(time, member)]
        self.raid_until = {}    # guild -> time the raid lockdown ends

    # ------------------------------------------------------------ helpers ---
    async def enabled(self, guild_id, column):
        return bool(await self.bot.db.get_setting(guild_id, column, 0))

    @staticmethod
    def exempt(member):
        perms = member.guild_permissions
        return perms.administrator or perms.manage_messages or perms.manage_guild

    async def timeout_member(self, member, reason):
        try:
            await member.timeout(
                datetime.timedelta(hours=config.AUTOMOD_TIMEOUT_HOURS), reason=f"{utils.AUTO_PREFIX} {reason}")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def notify(self, guild, title, fields, color=config.COLOR_LOG_DELETE):
        await utils.send_log(self.bot, guild, utils.log_embed(title, color, fields))

    async def link_allowed(self, member):
        rows = await self.bot.db.fetchall("SELECT target_id FROM link_allowed WHERE guild_id = ?", (member.guild.id,))
        allowed = {r["target_id"] for r in rows}
        return member.id in allowed or any(role.id in allowed for role in member.roles)

    # ---------------------------------------------------- spam and links ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        row = await self.bot.db.fetchone(
            "SELECT antispam, antilink FROM settings WHERE guild_id = ?", (message.guild.id,))
        if row is None or self.exempt(message.author):
            return
        if row["antispam"]:
            await self.check_spam(message)
        if row["antilink"]:
            await self.check_links(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.author.bot or after.guild is None or not isinstance(after.author, discord.Member):
            return
        if before.content == after.content or self.exempt(after.author):
            return
        if await self.enabled(after.guild.id, "antilink"):
            await self.check_links(after)

    async def check_spam(self, message):
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        window = self.recent.setdefault(key, collections.deque())
        window.append((now, message))
        while window and now - window[0][0] > config.SPAM_SECONDS:
            window.popleft()
        if len(window) <= config.SPAM_MESSAGES:
            return
        if now - self.punished.get(key, float("-inf")) < 10:
            return
        self.punished[key] = now
        spam = [m for _, m in window]
        window.clear()

        done = await self.timeout_member(message.author, "Anti-spam")
        for m in spam:
            try:
                await m.delete()
            except discord.HTTPException:
                pass
        await self.notify(message.guild, "Automod | Anti-spam", [
            ("👤 User:", utils.user_field(message.author)),
            ("📌 Channel:", message.channel.mention),
            ("⚙️ Action:", f"Timed out for {config.AUTOMOD_TIMEOUT_HOURS} hours" if done
             else "⚠️ I couldn't time them out (check my role position and permissions)"),
            ("📝 Reason:", f"More than {config.SPAM_MESSAGES} messages in {config.SPAM_SECONDS} seconds"),
            ("📅 Date:", utils.dt()),
        ])

    async def check_links(self, message):
        if not utils.URL_RE.search(message.content or ""):
            return
        if await self.link_allowed(message.author):
            return
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        done = await self.timeout_member(message.author, "Anti-link")
        await self.notify(message.guild, "Automod | Anti-link", [
            ("👤 User:", utils.user_field(message.author)),
            ("📌 Channel:", message.channel.mention),
            ("⚙️ Action:", f"Link deleted, timed out for {config.AUTOMOD_TIMEOUT_HOURS} hours" if done
             else "Link deleted. ⚠️ I couldn't time them out (check my role position and permissions)"),
            ("🔗 Message:", utils.code_block(message.content)),
            ("📅 Date:", utils.dt()),
        ])

    # ---------------------------------------------------------- anti-nuke ---
    async def audit_executor(self, guild, action, target_id):
        await asyncio.sleep(0.7)
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                if age < 15 and entry.target is not None and entry.target.id == target_id:
                    return entry.user
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    async def nuke_hit(self, guild, action, target_id, label):
        if not await self.enabled(guild.id, "antinuke"):
            return
        user = await self.audit_executor(guild, action, target_id)
        if user is None or user.id in (self.bot.user.id, guild.owner_id):
            return
        key = (guild.id, user.id)
        now = time.monotonic()
        window = self.nuke.setdefault(key, collections.deque())
        window.append(now)
        while window and now - window[0] > config.NUKE_WINDOW:
            window.popleft()
        if len(window) < config.NUKE_LIMIT:
            return
        window.clear()

        try:
            await guild.ban(user, reason=f"{utils.AUTO_PREFIX} Anti-nuke: mass {label}", delete_message_seconds=0)
            done = True
        except (discord.Forbidden, discord.HTTPException):
            done = False
        await self.notify(guild, "Automod | Anti-nuke", [
            ("👤 User:", utils.user_field(user)),
            ("💥 Detected:", f"{config.NUKE_LIMIT}+ {label} in {config.NUKE_WINDOW} seconds"),
            ("⚙️ Action:", "Banned" if done else "⚠️ I couldn't ban them (check my role position and permissions)"),
            ("📅 Date:", utils.dt()),
        ])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.nuke_hit(channel.guild, discord.AuditLogAction.channel_delete, channel.id, "channel deletions")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self.nuke_hit(role.guild, discord.AuditLogAction.role_delete, role.id, "role deletions")

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await self.nuke_hit(guild, discord.AuditLogAction.ban, user.id, "bans")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if await self.enabled(member.guild.id, "antinuke"):
            await self.nuke_hit(member.guild, discord.AuditLogAction.kick, member.id, "kicks")

    # ----------------------------------------------- anti-raid / blacklist ---
    async def kick_raider(self, member):
        try:
            await member.kick(reason=f"{utils.AUTO_PREFIX} Anti-raid")
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild

        if await self.bot.db.is_blacklisted(member.id):
            try:
                await member.kick(reason=f"{utils.AUTO_PREFIX} Blacklisted user")
                done = True
            except (discord.Forbidden, discord.HTTPException):
                done = False
            await self.notify(guild, "Blacklist", [
                ("👤 User:", utils.user_field(member)),
                ("⚙️ Action:", "Kicked (blacklisted)" if done else "⚠️ I couldn't kick them"),
                ("📅 Date:", utils.dt()),
            ])
            return

        if not await self.enabled(guild.id, "antiraid"):
            return
        now = time.monotonic()
        if self.raid_until.get(guild.id, 0) > now:
            await self.kick_raider(member)
            return

        window = self.joins.setdefault(guild.id, collections.deque())
        window.append((now, member))
        while window and now - window[0][0] > config.RAID_SECONDS:
            window.popleft()
        if len(window) < config.RAID_JOINS:
            return

        self.raid_until[guild.id] = now + config.RAID_LOCKDOWN_SECONDS
        raiders = [m for _, m in window]
        window.clear()
        kicked = 0
        for raider in raiders:
            if await self.kick_raider(raider):
                kicked += 1
        await self.notify(guild, "Automod | Anti-raid", [
            ("🚨 Detected:", f"{config.RAID_JOINS}+ joins in {config.RAID_SECONDS} seconds"),
            ("⚙️ Action:", f"Kicked **{kicked}** member(s). Every new member is kicked for the next "
                          f"{config.RAID_LOCKDOWN_SECONDS} seconds."),
            ("📅 Date:", utils.dt()),
        ])

    # ----------------------------------------------------------- toggles ---
    async def toggle(self, ctx, column, label, state):
        current = bool(await self.bot.db.get_setting(ctx.guild.id, column, 0))
        if state is None:
            status = "ON" if current else "OFF"
            return await ctx.send(embed=utils.make_embed(
                description=f"🛡️ **{label}** is currently **{status}**. Use `{config.PREFIX}{column} on` or `off`."))
        await self.bot.db.set_setting(ctx.guild.id, column, 1 if state == "on" else 0)
        await ctx.send(embed=utils.ok(f"**{label}** is now **{state.upper()}**."))

    @commands.hybrid_command(name="antispam", description="Turn the anti-spam on or off.")
    @app_commands.describe(state="on or off")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def antispam(self, ctx, state: Optional[Literal["on", "off"]] = None):
        await self.toggle(ctx, "antispam", "Anti-spam", state)

    @commands.hybrid_command(name="antilink", description="Turn the anti-link on or off.")
    @app_commands.describe(state="on or off")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def antilink(self, ctx, state: Optional[Literal["on", "off"]] = None):
        await self.toggle(ctx, "antilink", "Anti-link", state)

    @commands.hybrid_command(name="linkallow", description="Allow (or stop allowing) a member or role to post links.")
    @app_commands.describe(target="The member or role")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def linkallow(self, ctx, target: Union[discord.Member, discord.Role]):
        db = self.bot.db
        exists = await db.fetchone(
            "SELECT 1 FROM link_allowed WHERE guild_id = ? AND target_id = ?", (ctx.guild.id, target.id))
        if exists:
            await db.execute("DELETE FROM link_allowed WHERE guild_id = ? AND target_id = ?", (ctx.guild.id, target.id))
            await ctx.send(embed=utils.ok(f"{target.mention} can no longer post links."))
        else:
            await db.execute("INSERT INTO link_allowed (guild_id, target_id) VALUES (?, ?)", (ctx.guild.id, target.id))
            await ctx.send(embed=utils.ok(f"{target.mention} can now post links."))

    @commands.hybrid_command(name="antinuke", description="Turn the anti-nuke on or off.")
    @app_commands.describe(state="on or off")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antinuke(self, ctx, state: Optional[Literal["on", "off"]] = None):
        await self.toggle(ctx, "antinuke", "Anti-nuke", state)

    @commands.hybrid_command(name="antiraid", description="Turn the anti-raid on or off.")
    @app_commands.describe(state="on or off")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def antiraid(self, ctx, state: Optional[Literal["on", "off"]] = None):
        await self.toggle(ctx, "antiraid", "Anti-raid", state)

    # ---------------------------------------------------------- blacklist ---
    @commands.hybrid_group(name="blacklist", fallback="list", invoke_without_command=True,
                           description="Show the blacklisted users.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def blacklist(self, ctx):
        rows = await self.bot.db.fetchall("SELECT user_id, reason FROM blacklist ORDER BY added_at DESC LIMIT 25")
        if not rows:
            return await ctx.send(embed=utils.make_embed(description="The blacklist is empty."))
        lines = [f"<@{r['user_id']}> (`{r['user_id']}`) — {utils.clip(r['reason'] or 'No reason', 100)}" for r in rows]
        await ctx.send(embed=utils.make_embed("🚫 Blacklist", "\n".join(lines), config.COLOR_ERR))

    @blacklist.command(name="add", description="Blacklist a user: they get kicked from every server with this bot.")
    @app_commands.describe(user="The user to blacklist", reason="Why")
    @utils.owner_only()
    async def blacklist_add(self, ctx, user: discord.User, *, reason: str = "No reason provided"):
        if user.id in config.OWNER_IDS or user.id == self.bot.user.id:
            return await ctx.send(embed=utils.err("You can't blacklist that user."), ephemeral=True)
        if await self.bot.db.is_blacklisted(user.id):
            return await ctx.send(embed=utils.err("That user is already blacklisted."), ephemeral=True)
        await self.bot.db.execute(
            "INSERT INTO blacklist (user_id, reason, added_by, added_at) VALUES (?, ?, ?, ?)",
            (user.id, reason, ctx.author.id, int(time.time())))
        kicked = 0
        for guild in self.bot.guilds:
            member = guild.get_member(user.id)
            if member is None:
                continue
            try:
                await member.kick(reason=f"{utils.AUTO_PREFIX} Blacklisted: {reason}")
                kicked += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
        await ctx.send(embed=utils.ok(
            f"**{user}** has been blacklisted and kicked from **{kicked}** server(s). "
            f"They will be kicked automatically whenever they join a server with me."))

    @blacklist.command(name="remove", description="Remove a user from the blacklist.")
    @app_commands.describe(user="The user to remove")
    @utils.owner_only()
    async def blacklist_remove(self, ctx, user: discord.User):
        cursor = await self.bot.db.execute("DELETE FROM blacklist WHERE user_id = ?", (user.id,))
        if cursor.rowcount == 0:
            return await ctx.send(embed=utils.err("That user isn't blacklisted."), ephemeral=True)
        await ctx.send(embed=utils.ok(f"**{user}** has been removed from the blacklist."))


async def setup(bot):
    await bot.add_cog(Automod(bot))
