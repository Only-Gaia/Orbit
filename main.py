import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config  # noqa: E402
import utils  # noqa: E402
from database import Database  # noqa: E402

log = logging.getLogger("orbit")

EXTENSIONS = [
    "moderation",
    "logs",
    "invites",
    "levels",
    "giveaways",
    "automod",
    "extras",
    "economy",
    "games",
]


def extension_path(name):
    """Works whether the cog files are next to main.py or inside a 'cogs' folder."""
    if os.path.exists(os.path.join(BASE_DIR, "cogs", f"{name}.py")):
        return f"cogs.{name}"
    return name


class OrbitBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=discord.Intents.all(),
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
        )
        self.db = Database(config.DB_PATH)

    async def setup_hook(self):
        await self.db.connect()
        failed = []
        for name in EXTENSIONS:
            try:
                await self.load_extension(extension_path(name))
            except Exception:
                failed.append(name)
                log.exception("Could not load %s", name)
        if failed:
            log.error("These files failed to load: %s", ", ".join(failed))
        await self.tree.sync()

    async def close(self):
        await self.db.close()
        await super().close()

    async def on_ready(self):
        log.info("Logged in as %s (%s) in %d servers", self.user, self.user.id, len(self.guilds))
        await self.change_presence(activity=discord.Game(name=f"{config.PREFIX}help | /help"))

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return

        # Unwrap CommandInvokeError / HybridCommandError to get the real exception.
        while getattr(error, "original", None) is not None and error.original is not error:
            error = error.original

        if isinstance(error, commands.MissingPermissions):
            perms = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            text = f"You need the **{perms}** permission to use this command."
        elif isinstance(error, commands.BotMissingPermissions):
            perms = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
            text = f"I need the **{perms}** permission to do that."
        elif isinstance(error, commands.MissingRequiredArgument):
            usage = f"{config.PREFIX}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            text = f"Missing argument `{error.param.name}`.\nUsage: `{usage}`"
        elif isinstance(error, commands.UserInputError):
            text = f"Invalid input: {error}"
        elif isinstance(error, commands.CommandOnCooldown):
            text = f"Slow down! Try again in {error.retry_after:.1f}s."
        elif isinstance(error, commands.NoPrivateMessage):
            text = "This command can only be used in a server."
        elif isinstance(error, commands.CheckFailure):
            text = str(error) or "You can't use this command."
        elif isinstance(error, discord.Forbidden):
            text = "I don't have permission to do that. Check my role position and permissions."
        else:
            log.error("Unhandled error in command %s", ctx.command, exc_info=error)
            text = "Something went wrong. Please try again."

        try:
            await ctx.send(embed=utils.err(text), ephemeral=True)
        except discord.HTTPException:
            pass


async def main():
    if not config.TOKEN:
        raise SystemExit("Missing TOKEN. Add your bot token as an environment variable / Replit Secret named TOKEN.")
    discord.utils.setup_logging()
    bot = OrbitBot()
    async with bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
