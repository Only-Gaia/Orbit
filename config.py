import os

# The bot token is read from an environment variable (Replit "Secrets"), never written in the code.
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")

PREFIX = "."
BOT_NAME = "Orbit"
DB_PATH = os.getenv("DB_PATH", "orbit.db")

# Users allowed to use owner-only commands (economy add/remove, blacklist add/remove).
OWNER_IDS = {1520803701692829806}

CURRENCY_NAME = "Orbit Token"
CURRENCY_EMOJI = "🪙"

COLOR_MAIN = 0x7C6CF0
COLOR_OK = 0x57F287
COLOR_ERR = 0xED4245
COLOR_WARN = 0xFEE75C
COLOR_GIVEAWAY = 0xEB459E
COLOR_LOG_EDIT = 0xF1C40F
COLOR_LOG_DELETE = 0xE74C3C
COLOR_LOG_INFO = 0x3498DB

# Automod
AUTOMOD_TIMEOUT_HOURS = 2
SPAM_MESSAGES = 5        # more than this many messages...
SPAM_SECONDS = 3         # ...inside this many seconds = spam
NUKE_LIMIT = 3           # destructive actions by one user...
NUKE_WINDOW = 10         # ...inside this many seconds = nuke attempt
RAID_JOINS = 5           # this many joins...
RAID_SECONDS = 10        # ...inside this many seconds = raid
RAID_LOCKDOWN_SECONDS = 120
