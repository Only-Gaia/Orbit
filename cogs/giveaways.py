import asyncio
import logging
import random
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import utils

log = logging.getLogger("orbit")


# ------------------------------------------------------------------ embeds ---
def giveaway_embed(gw, participants, state="active"):
    embed = discord.Embed(color=config.COLOR_GIVEAWAY, timestamp=discord.utils.utcnow())
    if state == "cancelled":
        embed.title = "🚫 | GIVEAWAY CANCELLED"
        embed.color = config.COLOR_ERR
    elif state == "ended":
        embed.title = "🎉 | GIVEAWAY ENDED"
        embed.color = 0x99AAB5
    else:
        embed.title = "🎉 | 🎉 GIVEAWAY 🎉"

    text = f"## 🎁 {gw['prize']}\n"
    if gw["description"]:
        text += f"{gw['description']}\n\n"
    if state == "active":
        text += "🎉 Press **Join** below to enter!"
    embed.description = text

    end = gw["end_time"]
    embed.add_field(name="⏳ Ends:" if state == "active" else "⏳ Ended:", value=f"<t:{end}:R>\n<t:{end}:F>")
    embed.add_field(name="🏆 Winners:", value=f"`{gw['winners']}`")
    embed.add_field(name="👥 Participants:", value=f"`{participants}`")

    requirements = []
    if gw["req_role"]:
        requirements.append(f"🎭 You must have <@&{gw['req_role']}>")
    if gw["blocked_role"]:
        requirements.append(f"🚫 You must **not** have <@&{gw['blocked_role']}>")
    if gw["req_invites"]:
        requirements.append(f"📨 You need at least `{gw['req_invites']}` invites")
    if requirements:
        embed.add_field(name="📋 Requirements:", value="\n".join(requirements), inline=False)

    embed.add_field(name="👑 Hosted by:", value=f"<@{gw['host_id']}>", inline=False)
    embed.set_footer(text=f"Orbit • Giveaway #{gw['id']}")
    return embed


def winner_embed(gw, winners, participants):
    if not winners:
        embed = discord.Embed(title="🎉 | ⌛ NO WINNER", color=config.COLOR_ERR)
        embed.description = f"## 🎁 {gw['prize']}\n\nNobody claimed the prize."
    else:
        title = "WE HAVE WINNERS!" if len(winners) > 1 else "WE HAVE A WINNER!"
        embed = discord.Embed(title=f"🎉 | 🎊 {title}", color=config.COLOR_WARN)
        lines = [
            f"🥇🥳 Congratulations <@{w['user_id']}>!" + (" ✅ *claimed*" if w["claimed"] else "")
            for w in winners
        ]
        embed.description = (
            f"## 🎁 {gw['prize']}\n\n" + "\n".join(lines)
            + "\n\n⚡ Press 🎟️ **Claim prize** below to open a ticket."
        )
        embed.add_field(name="⏳ Time to claim:", value=f"`{utils.format_duration(gw['reroll_seconds'])}`")
        embed.add_field(name="⚠️ Warning:", value="If you don't claim in time, an automatic **reroll** will happen. 🔄")
    embed.add_field(name="👥 Participants:", value=f"`{participants}`")
    embed.set_footer(text=f"Orbit • Giveaway #{gw['id']}")
    embed.timestamp = discord.utils.utcnow()
    return embed


# ------------------------------------------------------------------- views ---
class GiveawayView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Join", emoji="🎉", style=discord.ButtonStyle.success, custom_id="orbit:gw:join")
    async def join(self, interaction, button):
        await self.cog.handle_join(interaction)

    @discord.ui.button(label="Participants", emoji="👥", style=discord.ButtonStyle.secondary,
                       custom_id="orbit:gw:list")
    async def participants(self, interaction, button):
        await self.cog.handle_participants(interaction)


class ClaimView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Claim prize", emoji="🎟️", style=discord.ButtonStyle.success,
                       custom_id="orbit:gw:claim")
    async def claim(self, interaction, button):
        await self.cog.handle_claim(interaction)


class DeliveredView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Delivered", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id="orbit:gw:delivered")
    async def delivered(self, interaction, button):
        await self.cog.handle_delivered(interaction)


# --------------------------------------------------------------------- cog ---
class Giveaways(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._locks = {}

    def lock(self, gid):
        return self._locks.setdefault(gid, asyncio.Lock())

    async def cog_load(self):
        self.bot.add_view(GiveawayView(self))
        self.bot.add_view(ClaimView(self))
        self.bot.add_view(DeliveredView(self))
        self.checker.start()

    async def cog_unload(self):
        self.checker.cancel()

    # ------------------------------------------------------------ helpers ---
    async def get_gw(self, gid):
        return await self.bot.db.fetchone("SELECT * FROM giveaways WHERE id = ?", (gid,))

    async def count_entries(self, gid):
        row = await self.bot.db.fetchone("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?", (gid,))
        return row["c"]

    async def entry_ids(self, gid):
        rows = await self.bot.db.fetchall("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (gid,))
        return [r["user_id"] for r in rows]

    async def winner_rows(self, gid):
        return await self.bot.db.fetchall(
            "SELECT user_id, claimed FROM giveaway_winners WHERE giveaway_id = ? ORDER BY rowid", (gid,))

    async def requirements_error(self, member, gw):
        if gw["blocked_role"] and member.get_role(gw["blocked_role"]):
            return f"You can't join: you have the blocked role <@&{gw['blocked_role']}>."
        if gw["req_role"] and not member.get_role(gw["req_role"]):
            return f"You need the <@&{gw['req_role']}> role to join."
        if gw["req_invites"]:
            row = await self.bot.db.fetchone(
                "SELECT COUNT(*) AS c FROM invite_joins WHERE guild_id = ? AND inviter_id = ? AND has_left = 0",
                (gw["guild_id"], member.id))
            if row["c"] < gw["req_invites"]:
                return f"You need at least **{gw['req_invites']}** invites to join (you have **{row['c']}**)."
        return None

    def get_channel(self, gw):
        guild = self.bot.get_guild(gw["guild_id"])
        return guild, (guild.get_channel(gw["channel_id"]) if guild else None)

    async def edit_message(self, gw, message_id, **kwargs):
        _, channel = self.get_channel(gw)
        if channel is None or not message_id:
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(**kwargs)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------- autocomplete ---
    async def _giveaway_choices(self, interaction: discord.Interaction, current: str, statuses):
        """Build autocomplete choices: shows the giveaway's PRIZE (name), value is its internal id."""
        guild_id = interaction.guild.id if interaction.guild else None
        placeholders = ",".join("?" for _ in statuses)
        rows = await self.bot.db.fetchall(
            f"SELECT id, prize FROM giveaways WHERE guild_id = ? AND status IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 50",
            (guild_id, *statuses),
        )
        current = (current or "").lower()
        choices = []
        for r in rows:
            label = f"{r['prize']} (#{r['id']})"
            if current in r["prize"].lower() or current in str(r["id"]):
                choices.append(app_commands.Choice(name=label[:100], value=r["id"]))
        return choices[:25]

    async def active_giveaway_autocomplete(self, interaction: discord.Interaction, current: str):
        # /giveaway end only makes sense on giveaways still running
        return await self._giveaway_choices(interaction, current, ("active",))

    async def cancellable_giveaway_autocomplete(self, interaction: discord.Interaction, current: str):
        # /giveaway cancel makes sense on anything not already cancelled
        return await self._giveaway_choices(interaction, current, ("active", "ended"))

    # ------------------------------------------------------ background loop ---
    @tasks.loop(seconds=5)
    async def checker(self):
        now = int(time.time())
        db = self.bot.db
        try:
            for row in await db.fetchall(
                    "SELECT id FROM giveaways WHERE status = 'active' AND end_time <= ?", (now,)):
                await self.end_giveaway(row["id"])
            for row in await db.fetchall(
                    "SELECT id FROM giveaways WHERE status = 'ended' AND claim_deadline IS NOT NULL "
                    "AND claim_deadline <= ?", (now,)):
                await self.reroll_unclaimed(row["id"])
        except Exception:
            log.exception("Giveaway checker failed")

    @checker.before_loop
    async def before_checker(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------- end / reroll ---
    async def end_giveaway(self, gid):
        async with self.lock(gid):
            gw = await self.get_gw(gid)
            if gw is None or gw["status"] != "active":
                return False
            db = self.bot.db
            await db.execute("UPDATE giveaways SET status = 'ended' WHERE id = ?", (gid,))
            guild, channel = self.get_channel(gw)
            if channel is None:
                return False

            entries = await self.entry_ids(gid)
            pool = []
            for uid in entries:
                member = guild.get_member(uid)
                if member and await self.requirements_error(member, gw) is None:
                    pool.append(uid)
            winners = random.sample(pool, min(gw["winners"], len(pool)))

            await self.edit_message(gw, gw["message_id"], embed=giveaway_embed(gw, len(entries), "ended"), view=None)
            if not winners:
                await channel.send(embed=utils.make_embed(
                    description=f"😔 Giveaway **#{gid}** (**{gw['prize']}**) ended: no valid participants."))
                return True

            for uid in winners:
                await db.execute("INSERT OR IGNORE INTO giveaway_winners (giveaway_id, user_id) VALUES (?, ?)",
                                 (gid, uid))
            rows = await self.winner_rows(gid)
            message = await channel.send(
                content=" ".join(f"<@{u}>" for u in winners),
                embed=winner_embed(gw, rows, len(entries)),
                view=ClaimView(self),
            )
            deadline = int(time.time()) + gw["reroll_seconds"]
            await db.execute("UPDATE giveaways SET result_message_id = ?, claim_deadline = ? WHERE id = ?",
                             (message.id, deadline, gid))
            return True

    async def reroll_unclaimed(self, gid):
        async with self.lock(gid):
            gw = await self.get_gw(gid)
            if (gw is None or gw["status"] != "ended" or not gw["claim_deadline"]
                    or gw["claim_deadline"] > time.time()):
                return
            db = self.bot.db
            guild, channel = self.get_channel(gw)
            if channel is None:
                await db.execute("UPDATE giveaways SET claim_deadline = NULL WHERE id = ?", (gid,))
                return

            winners = await self.winner_rows(gid)
            unclaimed = [w for w in winners if not w["claimed"]]
            if not unclaimed:
                await db.execute("UPDATE giveaways SET claim_deadline = NULL WHERE id = ?", (gid,))
                return

            taken = {w["user_id"] for w in winners}
            entries = await self.entry_ids(gid)
            pool = []
            for uid in entries:
                member = guild.get_member(uid)
                if uid not in taken and member and await self.requirements_error(member, gw) is None:
                    pool.append(uid)
            random.shuffle(pool)

            notes, new_winners = [], []
            for old in unclaimed:
                await db.execute("DELETE FROM giveaway_winners WHERE giveaway_id = ? AND user_id = ?",
                                 (gid, old["user_id"]))
                if pool:
                    new = pool.pop()
                    new_winners.append(new)
                    await db.execute("INSERT OR IGNORE INTO giveaway_winners (giveaway_id, user_id) VALUES (?, ?)",
                                     (gid, new))
                    notes.append(f"🔄 <@{old['user_id']}> didn't claim in time. New winner: <@{new}>!")
                else:
                    notes.append(f"⌛ <@{old['user_id']}> didn't claim in time and nobody is left to reroll.")

            rows = await self.winner_rows(gid)
            still_unclaimed = any(not r["claimed"] for r in rows)
            deadline = int(time.time()) + gw["reroll_seconds"] if still_unclaimed else None
            await db.execute("UPDATE giveaways SET claim_deadline = ? WHERE id = ?", (deadline, gid))

            kwargs = {"embed": winner_embed(gw, rows, len(entries))}
            if not still_unclaimed:
                kwargs["view"] = None
            await self.edit_message(gw, gw["result_message_id"], **kwargs)
            try:
                await channel.send(content=" ".join(f"<@{u}>" for u in new_winners) or None,
                                   embed=utils.make_embed(
                                       f"🔄 Giveaway #{gid} reroll", "\n".join(notes), config.COLOR_WARN))
            except discord.HTTPException:
                pass

    # --------------------------------------------------------- button logic ---
    async def handle_join(self, interaction):
        db = self.bot.db
        gw = await db.fetchone("SELECT * FROM giveaways WHERE message_id = ?", (interaction.message.id,))
        if gw is None or gw["status"] != "active":
            return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)

        member = interaction.user
        existing = await db.fetchone(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (gw["id"], member.id))
        if existing:
            await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
                             (gw["id"], member.id))
            text = "You left the giveaway."
        else:
            problem = await self.requirements_error(member, gw)
            if problem:
                return await interaction.response.send_message(problem, ephemeral=True)
            await db.execute("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
                             (gw["id"], member.id))
            text = "🎉 You joined the giveaway, good luck! (Press **Join** again to leave.)"
        await interaction.response.send_message(text, ephemeral=True)
        try:
            await interaction.message.edit(embed=giveaway_embed(gw, await self.count_entries(gw["id"])))
        except discord.HTTPException:
            pass

    async def handle_participants(self, interaction):
        gw = await self.bot.db.fetchone("SELECT id FROM giveaways WHERE message_id = ?", (interaction.message.id,))
        if gw is None:
            return await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        rows = await self.bot.db.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ? LIMIT 40", (gw["id"],))
        total = await self.count_entries(gw["id"])
        embed = utils.make_embed(f"👥 Participants ({total})",
                                 ", ".join(f"<@{r['user_id']}>" for r in rows) or "Nobody yet.")
        if total > 40:
            embed.set_footer(text=f"...and {total - 40} more")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_claim(self, interaction):
        db = self.bot.db
        gw = await db.fetchone("SELECT * FROM giveaways WHERE result_message_id = ?", (interaction.message.id,))
        if gw is None or gw["status"] != "ended":
            return await interaction.response.send_message("This giveaway is no longer available.", ephemeral=True)

        async with self.lock(gw["id"]):
            winner = await db.fetchone(
                "SELECT claimed FROM giveaway_winners WHERE giveaway_id = ? AND user_id = ?",
                (gw["id"], interaction.user.id))
            if winner is None:
                return await interaction.response.send_message("You're not a winner of this giveaway.", ephemeral=True)
            if winner["claimed"]:
                return await interaction.response.send_message("You already claimed this prize.", ephemeral=True)

            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            user = interaction.user
            host = guild.get_member(gw["host_id"])

            access = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True,
                    read_message_history=True, embed_links=True),
                user: access,
            }
            if host:
                overwrites[host] = access

            safe_name = re.sub(r"[^a-z0-9-]", "", user.name.lower())[:20] or "winner"
            try:
                ticket = await guild.create_text_channel(
                    f"prize-{safe_name}",
                    category=getattr(interaction.channel, "category", None),
                    overwrites=overwrites,
                    topic=f"gw:{gw['id']}:{gw['host_id']}:{user.id}",
                    reason=f"Giveaway #{gw['id']} prize claim",
                )
            except discord.HTTPException as e:
                return await interaction.followup.send(f"I couldn't create the ticket: {e}", ephemeral=True)

            await db.execute("UPDATE giveaway_winners SET claimed = 1 WHERE giveaway_id = ? AND user_id = ?",
                             (gw["id"], user.id))
            rows = await self.winner_rows(gw["id"])
            all_claimed = all(r["claimed"] for r in rows)
            if all_claimed:
                await db.execute("UPDATE giveaways SET claim_deadline = NULL WHERE id = ?", (gw["id"],))

            ticket_embed = utils.make_embed(
                "🎟️ Prize ticket",
                f"Congratulations {user.mention}! You won **{gw['prize']}**.\n\n"
                f"{host.mention if host else 'A staff member'} will deliver your prize here.\n"
                f"When it's delivered, press **Delivered** to close this ticket.",
                config.COLOR_WARN,
            )
            ticket_embed.set_footer(text=f"Orbit • Giveaway #{gw['id']}")
            await ticket.send(content=f"{user.mention} {host.mention if host else ''}".strip(),
                              embed=ticket_embed, view=DeliveredView(self))

            kwargs = {"embed": winner_embed(gw, rows, await self.count_entries(gw["id"]))}
            if all_claimed:
                kwargs["view"] = None
            try:
                await interaction.message.edit(**kwargs)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"🎟️ Your ticket is ready: {ticket.mention}", ephemeral=True)

    async def handle_delivered(self, interaction):
        topic = getattr(interaction.channel, "topic", None) or ""
        match = re.match(r"^gw:(\d+):(\d+):(\d+)$", topic)
        if not match:
            return await interaction.response.send_message("This isn't a prize ticket.", ephemeral=True)
        host_id = int(match.group(2))
        perms = interaction.user.guild_permissions
        if interaction.user.id != host_id and not (perms.administrator or perms.manage_channels):
            return await interaction.response.send_message(
                "Only the giveaway host or staff can mark this as delivered.", ephemeral=True)
        await interaction.response.send_message("✅ Marked as delivered. Closing this ticket in 3 seconds...")
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Giveaway prize delivered (marked by {interaction.user})")
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------ commands ---
    @commands.hybrid_group(name="giveaway", aliases=["gw"], invoke_without_command=True,
                           description="Create and manage giveaways.")
    @commands.guild_only()
    async def giveaway(self, ctx):
        await ctx.send(embed=utils.err(
            f"Usage: `{config.PREFIX}giveaway create`, `{config.PREFIX}giveaway cancel <name>`, "
            f"`{config.PREFIX}giveaway end <name>`. With slash commands (`/giveaway create`) Discord shows "
            f"every option for you, and `end`/`cancel` let you pick the giveaway by its name."), ephemeral=True)

    @giveaway.command(name="create", description="Start a giveaway.")
    @app_commands.describe(
        prize="What you are giving away",
        channel="Where to post the giveaway",
        duration="How long it lasts, e.g. 10m, 2h, 1d",
        winners="How many winners",
        reroll="How long winners have to claim before the automatic reroll, e.g. 1m, 5m",
        invites="(optional) invites required to join",
        required_role="(optional) role required to join",
        blocked_role="(optional) role that can't join",
        description="(optional) extra text shown in the giveaway",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def giveaway_create(self, ctx, prize: str, channel: discord.TextChannel, duration: str,
                              winners: commands.Range[int, 1, 20], reroll: str,
                              invites: commands.Range[int, 0, 1000] = 0,
                              required_role: Optional[discord.Role] = None,
                              blocked_role: Optional[discord.Role] = None,
                              *, description: Optional[str] = None):
        seconds = utils.parse_duration(duration)
        if not seconds or seconds < 10 or seconds > 60 * 86400:
            return await ctx.send(embed=utils.err("Invalid duration (min 10s, max 60d). Examples: `10m`, `2h`, `1d`."),
                                  ephemeral=True)
        claim = utils.parse_duration(reroll)
        if not claim or claim < 30 or claim > 86400:
            return await ctx.send(embed=utils.err("The reroll time must be between `30s` and `1d`."), ephemeral=True)
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(embed=utils.err(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}."), ephemeral=True)

        cursor = await self.bot.db.execute(
            "INSERT INTO giveaways (guild_id, channel_id, prize, description, host_id, winners, end_time, "
            "reroll_seconds, req_invites, req_role, blocked_role) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx.guild.id, channel.id, prize, description, ctx.author.id, winners, int(time.time()) + seconds,
             claim, invites, required_role.id if required_role else None,
             blocked_role.id if blocked_role else None))
        gid = cursor.lastrowid
        gw = await self.get_gw(gid)
        message = await channel.send(embed=giveaway_embed(gw, 0), view=GiveawayView(self))
        await self.bot.db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message.id, gid))
        await ctx.send(embed=utils.ok(f"Giveaway **#{gid}** created in {channel.mention}. [Jump]({message.jump_url})"))

    @giveaway.command(name="cancel", description="Cancel a giveaway.")
    @app_commands.describe(giveaway_id="The giveaway to cancel (start typing its name)")
    @app_commands.rename(giveaway_id="giveaway")
    @app_commands.autocomplete(giveaway_id=cancellable_giveaway_autocomplete)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def giveaway_cancel(self, ctx, giveaway_id: int):
        gw = await self.get_gw(giveaway_id)
        if gw is None or gw["guild_id"] != ctx.guild.id:
            return await ctx.send(embed=utils.err("Giveaway not found."), ephemeral=True)
        if gw["status"] == "cancelled":
            return await ctx.send(embed=utils.err("That giveaway is already cancelled."), ephemeral=True)
        async with self.lock(giveaway_id):
            await self.bot.db.execute(
                "UPDATE giveaways SET status = 'cancelled', claim_deadline = NULL WHERE id = ?", (giveaway_id,))
            count = await self.count_entries(giveaway_id)
            await self.edit_message(gw, gw["message_id"], embed=giveaway_embed(gw, count, "cancelled"), view=None)
            if gw["result_message_id"]:
                await self.edit_message(
                    gw, gw["result_message_id"], view=None,
                    embed=utils.make_embed("🚫 | GIVEAWAY CANCELLED", f"## 🎁 {gw['prize']}", config.COLOR_ERR))
        await ctx.send(embed=utils.ok(f"Giveaway **{gw['prize']}** (#{giveaway_id}) cancelled."))

    @giveaway.command(name="end", description="End a giveaway right now and pick the winners.")
    @app_commands.describe(giveaway_id="The giveaway to end now (start typing its name)")
    @app_commands.rename(giveaway_id="giveaway")
    @app_commands.autocomplete(giveaway_id=active_giveaway_autocomplete)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def giveaway_end(self, ctx, giveaway_id: int):
        gw = await self.get_gw(giveaway_id)
        if gw is None or gw["guild_id"] != ctx.guild.id or gw["status"] != "active":
            return await ctx.send(embed=utils.err("Active giveaway not found."), ephemeral=True)
        await self.bot.db.execute("UPDATE giveaways SET end_time = ? WHERE id = ?", (int(time.time()), giveaway_id))
        await self.end_giveaway(giveaway_id)
        await ctx.send(embed=utils.ok(f"Giveaway **{gw['prize']}** (#{giveaway_id}) ended."))


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
