import re
from typing import Optional

import discord
from discord.ext import commands

import config

# Prefix used in audit-log reasons for actions taken automatically by the bot.
AUTO_PREFIX = "[Auto]"

URL_RE = re.compile(
    r"(?i)(?:https?://|www\.|discord\.gg/|discord(?:app)?\.com/invite/)\S+"
    r"|\b[a-z0-9-]{2,}\.(?:com|net|org|io|gg|me|xyz|co|ly|tv|app|dev|info|ru|cn|tk|ml|ga|cf|gq|link|site|online|store)\b"
)

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])")


# ---------------------------------------------------------------- parsing ---
def parse_duration(text: str) -> Optional[int]:
    """'10m' -> 600, '1h30m' -> 5400, '2d' -> 172800. Returns None if invalid."""
    text = text.strip().lower()
    parts = _DURATION_RE.findall(text)
    if not parts or _DURATION_RE.sub("", text).strip():
        return None
    return sum(int(n) * _UNITS[u] for n, u in parts) or None


def format_duration(seconds) -> str:
    seconds = int(seconds)
    parts = []
    for name, size in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value} {name}{'s' if value != 1 else ''}")
    return " ".join(parts) or "0 seconds"


def parse_amount(text: str, balance: int) -> Optional[int]:
    """'100', '10k', '1.5m', 'half', 'all' -> int. Returns None if invalid."""
    t = text.lower().replace(",", "").replace("_", "").strip()
    if t in ("all", "max"):
        return balance
    if t == "half":
        return balance // 2
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmb]?)", t)
    if not match:
        return None
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[match.group(2)]
    return int(float(match.group(1)) * multiplier)


def fmt(n) -> str:
    return f"{int(n):,}"


def clip(text, limit: int = 1000) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def code_block(text, limit: int = 900) -> str:
    safe = clip(text, limit).replace("```", "'''")
    return f"```{safe}```"


# ----------------------------------------------------------------- embeds ---
def make_embed(title=None, description=None, color=config.COLOR_MAIN) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def ok(text) -> discord.Embed:
    return discord.Embed(description=f"✅ {text}", color=config.COLOR_OK)


def err(text) -> discord.Embed:
    return discord.Embed(description=f"❌ {text}", color=config.COLOR_ERR)


def log_embed(title, color, fields) -> discord.Embed:
    """Log-style embed (like the 'Message edited' screenshot): a title and a list of (name, value)."""
    embed = discord.Embed(title=f"| {title}", color=color, timestamp=discord.utils.utcnow())
    for name, value in fields:
        embed.add_field(name=name, value=clip(value, 1024), inline=False)
    return embed


def user_field(user) -> str:
    return f"{user.mention}\n`{user.id}`"


def dt(when=None, style="F") -> str:
    return discord.utils.format_dt(when or discord.utils.utcnow(), style)


def audit_reason(moderator, reason) -> str:
    return f"{moderator} ({moderator.id}): {reason}"[:500]


# -------------------------------------------------------------- hierarchy ---
def hierarchy_error(author, target, *, allow_self=False, check_bot=True) -> Optional[str]:
    guild = target.guild
    if target.id == guild.owner_id:
        return "You can't do that to the server owner."
    if target.id == author.id:
        return None if allow_self else "You can't do that to yourself."
    if author.id != guild.owner_id and author.top_role <= target.top_role:
        return "That member's highest role is equal to or higher than yours."
    if check_bot and guild.me.top_role <= target.top_role:
        return "That member's highest role is equal to or higher than mine."
    return None


def role_error(author, target, role) -> Optional[str]:
    """Checks for pex/depex: nobody can hand out (or take away) roles above their own rank."""
    guild = author.guild
    if role.is_default() or role.managed:
        return "That role can't be assigned manually."
    if guild.me.top_role <= role:
        return "That role is higher than (or equal to) my highest role."
    if author.id == guild.owner_id:
        return None
    if target.id == author.id:
        return "You can't add or remove roles on yourself."
    if role >= author.top_role:
        return "You can't manage a role that is equal to or higher than your highest role."
    if target.top_role >= author.top_role:
        return "You can't manage the roles of someone who is equal to or higher than you."
    return None


# ------------------------------------------------------------------- logs ---
async def send_log(bot, guild, embed) -> bool:
    row = await bot.db.fetchone("SELECT logs_channel FROM settings WHERE guild_id = ?", (guild.id,))
    if not row or not row["logs_channel"]:
        return False
    channel = guild.get_channel(row["logs_channel"])
    if channel is None:
        return False
    try:
        await channel.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


# ----------------------------------------------------------------- checks ---
def owner_only():
    async def predicate(ctx):
        if ctx.author.id in config.OWNER_IDS:
            return True
        raise commands.CheckFailure("Only the bot owner can use this command.")
    return commands.check(predicate)


# ------------------------------------------------------------- UI helpers ---
class ConfirmView(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.value = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction, button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction, button):
        self.value = False
        await interaction.response.defer()
        self.stop()


async def confirm(ctx, text) -> bool:
    view = ConfirmView(ctx.author.id)
    message = await ctx.send(embed=make_embed(description=f"⚠️ {text}", color=config.COLOR_WARN), view=view)
    await view.wait()
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    return bool(view.value)


class FieldsModal(discord.ui.Modal):
    """A popup form. fields = {key: kwargs for discord.ui.TextInput}."""

    def __init__(self, title, fields):
        super().__init__(title=title[:45], timeout=600)
        self.inputs = {}
        self.submitted = False
        for key, options in fields.items():
            item = discord.ui.TextInput(**options)
            self.inputs[key] = item
            self.add_item(item)

    def value(self, key) -> str:
        return (self.inputs[key].value or "").strip()

    async def on_submit(self, interaction):
        self.submitted = True
        await interaction.response.send_message("✅ Got it!", ephemeral=True)
        self.stop()


class _OpenModalView(discord.ui.View):
    def __init__(self, author_id, modal):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.modal = modal

    @discord.ui.button(label="Open form", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_form(self, interaction, button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("This form isn't for you.", ephemeral=True)
        await interaction.response.send_modal(self.modal)
        self.stop()


async def run_modal(ctx, modal) -> bool:
    """Shows a form. Slash command: opens it directly. Prefix command: sends a button that opens it."""
    prompt = None
    if ctx.interaction is not None and not ctx.interaction.response.is_done():
        await ctx.interaction.response.send_modal(modal)
    else:
        prompt = await ctx.send(
            embed=make_embed(description="📝 Click the button below to open the form."),
            view=_OpenModalView(ctx.author.id, modal),
        )
    await modal.wait()
    if prompt is not None:
        try:
            await prompt.delete()
        except discord.HTTPException:
            pass
    return modal.submitted
