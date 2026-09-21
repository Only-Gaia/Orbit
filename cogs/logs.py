import asyncio
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils


class Logs(commands.Cog):
    """Sends server events to the configured logs channel."""

    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------- helpers ---
    async def send(self, guild, embed):
        await utils.send_log(self.bot, guild, embed)

    async def recent_entry(self, guild, action, target_id, predicate=None, seconds=15):
        """Finds a recent audit-log entry (who did it). Returns None if not found / no permission."""
        try:
            async for entry in guild.audit_logs(limit=10, action=action):
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                if age > seconds or entry.target is None or entry.target.id != target_id:
                    continue
                if predicate and not predicate(entry):
                    continue
                return entry
        except (discord.Forbidden, discord.HTTPException):
            pass
        return None

    def describe(self, entry):
        """Returns (who, reason, is_auto) for an audit-log entry."""
        if entry is None or entry.user is None:
            return "Unknown", "—", False
        reason = entry.reason or ""
        if reason.startswith(utils.AUTO_PREFIX):
            return entry.user.mention, reason, True
        match = re.match(r"^.*? \((\d+)\): (.*)$", reason, re.S)
        if entry.user.id == self.bot.user.id and match:
            return f"<@{match.group(1)}>", match.group(2), False
        return entry.user.mention, reason or "No reason provided", False

    # ----------------------------------------------------------- messages ---
    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.guild is None or after.author.bot or before.content == after.content:
            return
        embed = utils.log_embed("Message edited", config.COLOR_LOG_EDIT, [
            ("👤 Author:", utils.user_field(after.author)),
            ("📌 Channel:", after.channel.mention),
            ("🔗 Jump to message:", f"[click here]({after.jump_url})"),
            ("➖ Before:", utils.code_block(before.content or " ")),
            ("➕ After:", utils.code_block(after.content or " ")),
            ("📅 Date:", utils.dt()),
        ])
        await self.send(after.guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot:
            return
        fields = [
            ("👤 Author:", utils.user_field(message.author)),
            ("📌 Channel:", message.channel.mention),
            ("🗑️ Content:", utils.code_block(message.content or "(no text)")),
        ]
        if message.attachments:
            fields.append(("📎 Attachments:", str(len(message.attachments))))
        fields.append(("📅 Date:", utils.dt()))
        await self.send(message.guild, utils.log_embed("Message deleted", config.COLOR_LOG_DELETE, fields))

    # ----------------------------------------------- roles / mute / unmute ---
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        guild = after.guild

        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            if added or removed:
                await asyncio.sleep(1)
                entry = await self.recent_entry(guild, discord.AuditLogAction.member_role_update, after.id)
                who, reason, auto = self.describe(entry)
                fields = [("👤 Member:", utils.user_field(after))]
                if added:
                    fields.append(("➕ Role added:", ", ".join(r.mention for r in added)))
                if removed:
                    fields.append(("➖ Role removed:", ", ".join(r.mention for r in removed)))
                fields += [("🛡️ By:", who), ("📝 Reason:", utils.clip(reason, 500)), ("📅 Date:", utils.dt())]
                await self.send(guild, utils.log_embed("Role update", config.COLOR_LOG_INFO, fields))

        if before.timed_out_until != after.timed_out_until:
            await asyncio.sleep(1)
            entry = await self.recent_entry(
                guild, discord.AuditLogAction.member_update, after.id,
                predicate=lambda e: any(key == "timed_out_until" for key, _ in e.after))
            who, reason, auto = self.describe(entry)
            if auto:
                return
            if after.timed_out_until is not None and after.timed_out_until > discord.utils.utcnow():
                fields = [
                    ("👤 Member:", utils.user_field(after)),
                    ("🛡️ Muted by:", who),
                    ("📝 Reason:", utils.clip(reason, 500)),
                    ("⏳ Until:", utils.dt(after.timed_out_until)),
                    ("📅 Date:", utils.dt()),
                ]
                await self.send(guild, utils.log_embed("Member muted", config.COLOR_LOG_DELETE, fields))
            elif before.timed_out_until is not None and after.timed_out_until is None:
                fields = [
                    ("👤 Member:", utils.user_field(after)),
                    ("🛡️ Unmuted by:", who),
                    ("📅 Date:", utils.dt()),
                ]
                await self.send(guild, utils.log_embed("Member unmuted", config.COLOR_OK, fields))

    # ----------------------------------------------------------- channels ---
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        entry = await self.recent_entry(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        if entry and entry.user and entry.user.id == self.bot.user.id:
            return
        who, _, _ = self.describe(entry)
        await self.send(channel.guild, utils.log_embed("Channel created", config.COLOR_OK, [
            ("📌 Channel:", f"{channel.mention} (`{channel.name}`)"),
            ("📁 Type:", str(channel.type).replace("_", " ")),
            ("🛡️ By:", who),
            ("📅 Date:", utils.dt()),
        ]))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await asyncio.sleep(1)
        entry = await self.recent_entry(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        if entry and entry.user and entry.user.id == self.bot.user.id:
            return
        who, _, _ = self.describe(entry)
        await self.send(channel.guild, utils.log_embed("Channel deleted", config.COLOR_LOG_DELETE, [
            ("📌 Channel:", f"`{channel.name}`"),
            ("📁 Type:", str(channel.type).replace("_", " ")),
            ("🛡️ By:", who),
            ("📅 Date:", utils.dt()),
        ]))

    # ------------------------------------------------- joins / leaves ---
    @commands.Cog.listener()
    async def on_orbit_member_join(self, member, inviter_id, code):
        inviter = f"<@{inviter_id}>\n`{code}`" if inviter_id else "Unknown (vanity link, or I can't see invites)"
        await self.send(member.guild, utils.log_embed("Member joined", config.COLOR_OK, [
            ("👤 Member:", utils.user_field(member)),
            ("🗓️ Account created:", utils.dt(member.created_at, "R")),
            ("📨 Invited by:", inviter),
            ("👥 Members now:", str(member.guild.member_count)),
            ("📅 Date:", utils.dt()),
        ]))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        await asyncio.sleep(1)
        if await self.recent_entry(guild, discord.AuditLogAction.ban, member.id):
            return  # the ban log covers it
        entry = await self.recent_entry(guild, discord.AuditLogAction.kick, member.id)
        if entry is not None:
            who, reason, auto = self.describe(entry)
            if auto:
                return
            await self.send(guild, utils.log_embed("Member kicked", config.COLOR_LOG_DELETE, [
                ("👤 Member:", utils.user_field(member)),
                ("🛡️ Kicked by:", who),
                ("📝 Reason:", utils.clip(reason, 500)),
                ("📅 Date:", utils.dt()),
            ]))
            return
        if await self.bot.db.is_blacklisted(member.id):
            return
        fields = [("👤 Member:", utils.user_field(member))]
        if member.joined_at:
            fields.append(("🗓️ Joined:", utils.dt(member.joined_at, "R")))
        fields += [("👥 Members now:", str(guild.member_count)), ("📅 Date:", utils.dt())]
        await self.send(guild, utils.log_embed("Member left", config.COLOR_WARN, fields))

    # ------------------------------------------------------------ invites ---
    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild is None:
            return
        inviter = invite.inviter.mention if invite.inviter else "Unknown"
        uses = str(invite.max_uses) if invite.max_uses else "unlimited"
        await self.send(invite.guild, utils.log_embed("Invite created", config.COLOR_LOG_INFO, [
            ("🔗 Code:", f"`{invite.code}`"),
            ("📌 Channel:", invite.channel.mention if invite.channel else "Unknown"),
            ("👤 Created by:", inviter),
            ("🔢 Max uses:", uses),
            ("📅 Date:", utils.dt()),
        ]))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if invite.guild is None:
            return
        await self.send(invite.guild, utils.log_embed("Invite deleted", config.COLOR_LOG_DELETE, [
            ("🔗 Code:", f"`{invite.code}`"),
            ("📌 Channel:", invite.channel.mention if invite.channel else "Unknown"),
            ("📅 Date:", utils.dt()),
        ]))

    # ------------------------------------------------------ ban / unban ---
    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await asyncio.sleep(1)
        entry = await self.recent_entry(guild, discord.AuditLogAction.ban, user.id)
        who, reason, auto = self.describe(entry)
        if auto:
            return
        await self.send(guild, utils.log_embed("Member banned", config.COLOR_LOG_DELETE, [
            ("👤 User:", utils.user_field(user)),
            ("🛡️ Banned by:", who),
            ("📝 Reason:", utils.clip(reason, 500)),
            ("📅 Date:", utils.dt()),
        ]))

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        await asyncio.sleep(1)
        entry = await self.recent_entry(guild, discord.AuditLogAction.unban, user.id)
        who, reason, _ = self.describe(entry)
        await self.send(guild, utils.log_embed("Member unbanned", config.COLOR_OK, [
            ("👤 User:", utils.user_field(user)),
            ("🛡️ Unbanned by:", who),
            ("📝 Reason:", utils.clip(reason, 500)),
            ("📅 Date:", utils.dt()),
        ]))

    # ----------------------------------------------------------- commands ---
    @commands.hybrid_group(name="logs", fallback="status", invoke_without_command=True,
                           description="Show or configure the logs channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs(self, ctx):
        channel_id = await self.bot.db.get_setting(ctx.guild.id, "logs_channel")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if channel:
            await ctx.send(embed=utils.make_embed(description=f"📋 Logs are sent to {channel.mention}."))
        else:
            await ctx.send(embed=utils.make_embed(
                description=f"📋 Logs are disabled. Use `{config.PREFIX}logs set #channel` to enable them."))

    @logs.command(name="set", description="Send all logs to a channel.")
    @app_commands.describe(channel="The channel for the logs")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs_set(self, ctx, channel: discord.TextChannel):
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(embed=utils.err(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}."), ephemeral=True)
        await self.bot.db.set_setting(ctx.guild.id, "logs_channel", channel.id)
        await ctx.send(embed=utils.ok(f"Logs will now be sent in {channel.mention}."))

    @logs.command(name="disable", description="Turn the logs off.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs_disable(self, ctx):
        await self.bot.db.set_setting(ctx.guild.id, "logs_channel", None)
        await ctx.send(embed=utils.ok("Logs disabled."))


async def setup(bot):
    await bot.add_cog(Logs(bot))
