import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils

DEFAULT_WELCOME = "Welcome {user} to **{server}**! You are member #{count}. 🎉"
DEFAULT_GOODBYE = "**{name}** left **{server}**. We'll miss you! 👋"


def render(text, member):
    """Placeholders: {user} {name} {server} {count}"""
    return (text.replace("{user}", member.mention)
                .replace("{name}", member.display_name)
                .replace("{server}", member.guild.name)
                .replace("{count}", str(member.guild.member_count)))


class Extras(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stickies = {}
        self.counters = {}

    async def cog_load(self):
        rows = await self.bot.db.fetchall("SELECT channel_id, content FROM stickies")
        self.stickies = {r["channel_id"]: r["content"] for r in rows}

    async def fail(self, ctx, text):
        await ctx.send(embed=utils.err(text), ephemeral=True)

    # -------------------------------------------------- welcome / goodbye ---
    async def send_greeting(self, member, kind):
        row = await self.bot.db.fetchone(
            f"SELECT {kind}_channel AS channel, {kind}_message AS message FROM settings WHERE guild_id = ?",
            (member.guild.id,))
        if not row or not row["channel"]:
            return
        channel = member.guild.get_channel(row["channel"])
        if channel is None:
            return
        default = DEFAULT_WELCOME if kind == "welcome" else DEFAULT_GOODBYE
        embed = utils.make_embed(
            description=render(row["message"] or default, member),
            color=config.COLOR_OK if kind == "welcome" else config.COLOR_ERR)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not await self.bot.db.is_blacklisted(member.id):
            await self.send_greeting(member, "welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if not await self.bot.db.is_blacklisted(member.id):
            await self.send_greeting(member, "goodbye")

    async def greeting_status(self, ctx, kind):
        channel_id = await self.bot.db.get_setting(ctx.guild.id, f"{kind}_channel")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return await ctx.send(embed=utils.make_embed(
                description=f"The {kind} message is off. Use `{config.PREFIX}{kind} set #channel <message>`.\n"
                            f"Placeholders: `{{user}}` `{{name}}` `{{server}}` `{{count}}`"))
        message = await self.bot.db.get_setting(
            ctx.guild.id, f"{kind}_message", DEFAULT_WELCOME if kind == "welcome" else DEFAULT_GOODBYE)
        await ctx.send(embed=utils.make_embed(
            description=f"The {kind} message is sent in {channel.mention}:\n{utils.code_block(message)}"))

    async def greeting_set(self, ctx, kind, channel, message):
        await self.bot.db.set_setting(ctx.guild.id, f"{kind}_channel", channel.id)
        await self.bot.db.set_setting(ctx.guild.id, f"{kind}_message", message)
        await ctx.send(embed=utils.ok(f"The {kind} message will be sent in {channel.mention}."))

    async def greeting_disable(self, ctx, kind):
        await self.bot.db.set_setting(ctx.guild.id, f"{kind}_channel", None)
        await ctx.send(embed=utils.ok(f"The {kind} message has been turned off."))

    async def greeting_test(self, ctx, kind):
        default = DEFAULT_WELCOME if kind == "welcome" else DEFAULT_GOODBYE
        message = await self.bot.db.get_setting(ctx.guild.id, f"{kind}_message", default)
        await ctx.send(embed=utils.make_embed(description=render(message, ctx.author)))

    @commands.hybrid_group(name="welcome", fallback="status", invoke_without_command=True,
                           description="Configure the welcome message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def welcome(self, ctx):
        await self.greeting_status(ctx, "welcome")

    @welcome.command(name="set", description="Set the welcome channel and message.")
    @app_commands.describe(channel="Where to send it", message="Use {user} {name} {server} {count}")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def welcome_set(self, ctx, channel: discord.TextChannel, *, message: str = DEFAULT_WELCOME):
        await self.greeting_set(ctx, "welcome", channel, message)

    @welcome.command(name="disable", description="Turn the welcome message off.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def welcome_disable(self, ctx):
        await self.greeting_disable(ctx, "welcome")

    @welcome.command(name="test", description="Preview the welcome message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def welcome_test(self, ctx):
        await self.greeting_test(ctx, "welcome")

    @commands.hybrid_group(name="goodbye", fallback="status", invoke_without_command=True,
                           description="Configure the goodbye message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def goodbye(self, ctx):
        await self.greeting_status(ctx, "goodbye")

    @goodbye.command(name="set", description="Set the goodbye channel and message.")
    @app_commands.describe(channel="Where to send it", message="Use {user} {name} {server} {count}")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def goodbye_set(self, ctx, channel: discord.TextChannel, *, message: str = DEFAULT_GOODBYE):
        await self.greeting_set(ctx, "goodbye", channel, message)

    @goodbye.command(name="disable", description="Turn the goodbye message off.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def goodbye_disable(self, ctx):
        await self.greeting_disable(ctx, "goodbye")

    @goodbye.command(name="test", description="Preview the goodbye message.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def goodbye_test(self, ctx):
        await self.greeting_test(ctx, "goodbye")

    # -------------------------------------------------------- stick / unstick ---
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        content = self.stickies.get(message.channel.id)
        if content is None:
            return
        count = self.counters.get(message.channel.id, 0) + 1
        if count >= 2:
            self.counters[message.channel.id] = 0
            try:
                await message.channel.send(content, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
        else:
            self.counters[message.channel.id] = count

    @commands.hybrid_command(name="stick", description="Repeat a message every 2 messages in this channel.")
    @app_commands.describe(message="The message the bot will repeat")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def stick(self, ctx, *, message: str):
        if len(message) > 2000:
            return await self.fail(ctx, "The message can be at most 2000 characters.")
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO stickies (channel_id, guild_id, content) VALUES (?, ?, ?)",
            (ctx.channel.id, ctx.guild.id, message))
        self.stickies[ctx.channel.id] = message
        self.counters[ctx.channel.id] = 0
        await ctx.send(embed=utils.ok("Done! I'll repeat that message every 2 messages in this channel."))

    @commands.hybrid_command(name="unstick", description="Stop repeating the sticky message in this channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def unstick(self, ctx):
        if ctx.channel.id not in self.stickies:
            return await self.fail(ctx, "There is no sticky message in this channel.")
        await self.bot.db.execute("DELETE FROM stickies WHERE channel_id = ?", (ctx.channel.id,))
        self.stickies.pop(ctx.channel.id, None)
        self.counters.pop(ctx.channel.id, None)
        await ctx.send(embed=utils.ok("Sticky message removed."))

    # ------------------------------------------------------------ embed ---
    @commands.hybrid_command(name="embed", description="Create a custom embed using a form.")
    @app_commands.describe(channel="Where to send it (defaults to this channel)")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def embed_cmd(self, ctx, channel: Optional[discord.TextChannel] = None):
        target = channel or ctx.channel
        perms = target.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await self.fail(ctx, f"I need **Send Messages** and **Embed Links** in {target.mention}.")
        modal = utils.FieldsModal("Create embed", {
            "title": dict(label="Title", required=False, max_length=256),
            "description": dict(label="Description", style=discord.TextStyle.paragraph, max_length=4000),
            "color": dict(label="Color (hex, e.g. 7C6CF0)", required=False, max_length=7),
            "image": dict(label="Image URL", required=False, max_length=500),
            "footer": dict(label="Footer", required=False, max_length=200),
        })
        if not await utils.run_modal(ctx, modal):
            return
        try:
            color = int(modal.value("color").lstrip("#"), 16) if modal.value("color") else config.COLOR_MAIN
        except ValueError:
            color = config.COLOR_MAIN
        embed = discord.Embed(title=modal.value("title") or None, description=modal.value("description"), color=color)
        if modal.value("image").startswith("http"):
            embed.set_image(url=modal.value("image"))
        if modal.value("footer"):
            embed.set_footer(text=modal.value("footer"))
        try:
            await target.send(embed=embed)
        except discord.HTTPException as e:
            return await self.fail(ctx, f"I couldn't send the embed: {e}")
        await ctx.send(embed=utils.ok(f"Embed sent in {target.mention}."), ephemeral=True)

    # ------------------------------------------------------------- desk ---
    @commands.hybrid_group(name="desk", aliases=["desc"], fallback="show", invoke_without_command=True,
                           description="Show the server description.")
    @commands.guild_only()
    async def desk(self, ctx):
        text = await self.bot.db.get_setting(ctx.guild.id, "desk")
        if not text:
            return await self.fail(
                ctx, f"This server has no description yet. An admin can use `{config.PREFIX}desk create`.")
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    @desk.command(name="create", description="Create the server description (plain text, not an embed).")
    @app_commands.describe(text="The description (leave empty to write it in a form)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def desk_create(self, ctx, *, text: Optional[str] = None):
        if text is None:
            modal = utils.FieldsModal("Server description", {
                "text": dict(label="Description", style=discord.TextStyle.paragraph, max_length=2000)})
            if not await utils.run_modal(ctx, modal):
                return
            text = modal.value("text")
        if not text:
            return await self.fail(ctx, "The description can't be empty.")
        if len(text) > 2000:
            return await self.fail(ctx, "The description can be at most 2000 characters.")
        await self.bot.db.set_setting(ctx.guild.id, "desk", text)
        await ctx.send(embed=utils.ok(f"Server description saved. Use `{config.PREFIX}desk` to show it."))

    @desk.command(name="remove", description="Delete the server description.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def desk_remove(self, ctx):
        if not await self.bot.db.get_setting(ctx.guild.id, "desk"):
            return await self.fail(ctx, "There is no description to remove.")
        await self.bot.db.set_setting(ctx.guild.id, "desk", None)
        await ctx.send(embed=utils.ok("Server description removed."))

    # ----------------------------------------------------------- quests ---
    async def collect_lines(self, ctx, title, text):
        if text is None:
            modal = utils.FieldsModal(title, {
                "lines": dict(label="One per line (max 20)", style=discord.TextStyle.paragraph, max_length=4000)})
            if not await utils.run_modal(ctx, modal):
                return None
            text = modal.value("lines")
        lines = [line.strip() for line in re.split(r"[\n|]", text) if line.strip()]
        if not lines:
            await self.fail(ctx, "You didn't write anything.")
            return None
        if len(lines) > 20:
            await self.fail(ctx, f"You can create at most 20 items (you wrote {len(lines)}).")
            return None
        if any(len(line) > 190 for line in lines):
            await self.fail(ctx, "Each item can be at most 190 characters.")
            return None
        return lines

    async def show_quests(self, ctx, kind, title, hint):
        rows = await self.bot.db.fetchall(
            "SELECT text FROM quests WHERE guild_id = ? AND kind = ? ORDER BY position", (ctx.guild.id, kind))
        if not rows:
            return await self.fail(ctx, f"Nothing here yet. {hint}")
        body = "\n".join(f"**{i}.** {r['text']}" for i, r in enumerate(rows, 1))
        await ctx.send(embed=utils.make_embed(title, body))

    async def save_quests(self, ctx, kind, title, text):
        lines = await self.collect_lines(ctx, title, text)
        if lines is None:
            return
        db = self.bot.db
        await db.execute("DELETE FROM quests WHERE guild_id = ? AND kind = ?", (ctx.guild.id, kind))
        for position, line in enumerate(lines, 1):
            await db.execute("INSERT INTO quests (guild_id, kind, position, text) VALUES (?, ?, ?, ?)",
                             (ctx.guild.id, kind, position, line))
        body = "\n".join(f"**{i}.** {line}" for i, line in enumerate(lines, 1))
        await ctx.send(embed=utils.make_embed(title, body))

    @commands.hybrid_command(name="staffquest", description="Show the staff quests.")
    @commands.guild_only()
    async def staffquest(self, ctx):
        await self.show_quests(ctx, "staff", "📋 Staff quests",
                               f"An admin can create them with `{config.PREFIX}staffquestcreate`.")

    @commands.hybrid_command(name="staffquestcreate", description="Create the staff quests (max 20).")
    @app_commands.describe(text="One quest per line, or separated by | (leave empty to use a form)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def staffquestcreate(self, ctx, *, text: Optional[str] = None):
        await self.save_quests(ctx, "staff", "📋 Staff quests", text)

    @commands.hybrid_command(name="quest", description="Show the questions for the staff tryout.")
    @commands.guild_only()
    async def quest(self, ctx):
        await self.show_quests(ctx, "tryout", "📝 Staff tryout questions",
                               f"An admin can create them with `{config.PREFIX}questcreate`.")

    @commands.hybrid_command(name="questcreate", description="Create the staff tryout questions (max 20).")
    @app_commands.describe(text="One question per line, or separated by | (leave empty to use a form)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def questcreate(self, ctx, *, text: Optional[str] = None):
        await self.save_quests(ctx, "tryout", "📝 Staff tryout questions", text)

    @commands.hybrid_command(name="removequest", description="Remove all the staff tryout questions.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def removequest(self, ctx):
        cursor = await self.bot.db.execute(
            "DELETE FROM quests WHERE guild_id = ? AND kind = 'tryout'", (ctx.guild.id,))
        if cursor.rowcount == 0:
            return await self.fail(ctx, "There are no tryout questions to remove.")
        await ctx.send(embed=utils.ok(f"Removed **{cursor.rowcount}** tryout question(s)."))

    # ---------------------------------------------------- userinfo / serverinfo ---
    @commands.hybrid_command(name="userinfo", aliases=["ui"], description="Show information about a member.")
    @app_commands.describe(member="The member (defaults to you)")
    @commands.guild_only()
    async def userinfo(self, ctx, member: Optional[discord.Member] = None):
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        color = member.color if member.color.value else discord.Color(config.COLOR_MAIN)
        embed = discord.Embed(title=str(member), color=color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🆔 ID", value=f"`{member.id}`")
        embed.add_field(name="🏷️ Nickname", value=member.nick or "None")
        embed.add_field(name="🤖 Bot", value="Yes" if member.bot else "No")
        embed.add_field(name="🗓️ Account created", value=utils.dt(member.created_at, "R"))
        embed.add_field(name="📥 Joined server", value=utils.dt(member.joined_at, "R") if member.joined_at else "?")
        embed.add_field(name="⭐ Top role", value=member.top_role.mention)
        embed.add_field(name=f"🎭 Roles ({len(roles)})", value=utils.clip(" ".join(roles) or "None", 1000),
                        inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="serverinfo", aliases=["si"], description="Show information about this server.")
    @commands.guild_only()
    async def serverinfo(self, ctx):
        guild = ctx.guild
        embed = utils.make_embed(guild.name)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="🆔 ID", value=f"`{guild.id}`")
        embed.add_field(name="👑 Owner", value=f"<@{guild.owner_id}>")
        embed.add_field(name="🗓️ Created", value=utils.dt(guild.created_at, "R"))
        embed.add_field(name="👥 Members", value=f"`{guild.member_count}`")
        embed.add_field(name="💬 Channels", value=f"`{len(guild.text_channels)}` text • `{len(guild.voice_channels)}` voice")
        embed.add_field(name="🎭 Roles", value=f"`{len(guild.roles)}`")
        embed.add_field(name="🚀 Boosts", value=f"`{guild.premium_subscription_count}` (level {guild.premium_tier})")
        await ctx.send(embed=embed)

    # ------------------------------------------------------- invitebot ---
    @commands.hybrid_command(name="invitebot", description="Get the link to invite the bot to your server.")
    async def invitebot(self, ctx):
        url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=discord.Permissions(administrator=True),
            scopes=("bot", "applications.commands"),
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label=f"Invite {config.BOT_NAME}", style=discord.ButtonStyle.link, url=url, emoji="🚀"))
        embed = utils.make_embed(
            f"🚀 Invite {config.BOT_NAME}",
            f"Click the button below to add me to your server!\n\n[Or use this link]({url})")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await ctx.send(embed=embed, view=view)

    # ------------------------------------------------------------- help ---
    @commands.hybrid_command(name="help", description="Show every command.")
    async def help(self, ctx):
        p = config.PREFIX
        embed = utils.make_embed(
            f"{config.BOT_NAME} commands",
            f"Every command works with the prefix `{p}` and as a slash command `/`.")
        embed.add_field(name="🔨 Moderation", inline=False, value=(
            "`ban` `unban` `kick` `warn` `unwarn` `warns` `nick set` `nick reset` `pex` `depex` "
            "`mute` `unmute` `purge` `slowmode` `lock` `unlock`"))
        embed.add_field(name="📋 Support", inline=False, value=(
            "`logs set` `logs disable` `invites` `inviteboard` `reset invites` `reset allinvites` "
            "`level` `leaderboard` `rank` `messages` `reset messages` `reset allmessages` "
            "`giveaway create` `giveaway cancel` `giveaway end` `embed` `welcome` `goodbye` `stick` `unstick`"))
        embed.add_field(name="🛡️ Automod", inline=False, value=(
            "`antispam` `antilink` `linkallow` `antinuke` `antiraid` `userinfo` `serverinfo` "
            "`blacklist` `blacklist add` `blacklist remove`"))
        embed.add_field(name=f"{config.CURRENCY_EMOJI} Economy", inline=False, value=(
            "`cash` `work` `fish` `hunt` `blackjack` `roulette` `coinflip` `give` `add` `remove`"))
        embed.add_field(name="🎮 Fun", inline=False, value=(
            "`ship` `slap` `kiss` `fakekick` `kill` `hug` `aura` `gay` `8ball` `say` `marry` `divorce` `fakeban`"))
        embed.add_field(name="📌 Extra", inline=False, value=(
            "`desk` `desk create` `desk remove` `staffquest` `staffquestcreate` "
            "`quest` `questcreate` `removequest` `invitebot`"))
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Extras(bot))
