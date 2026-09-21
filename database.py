import aiosqlite

SETTING_COLUMNS = {
    "logs_channel", "welcome_channel", "welcome_message", "goodbye_channel",
    "goodbye_message", "antispam", "antilink", "antinuke", "antiraid", "desk",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER PRIMARY KEY,
    logs_channel INTEGER,
    welcome_channel INTEGER,
    welcome_message TEXT,
    goodbye_channel INTEGER,
    goodbye_message TEXT,
    antispam INTEGER NOT NULL DEFAULT 0,
    antilink INTEGER NOT NULL DEFAULT 0,
    antinuke INTEGER NOT NULL DEFAULT 0,
    antiraid INTEGER NOT NULL DEFAULT 0,
    desk TEXT
);

CREATE TABLE IF NOT EXISTS warns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS levels (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS invite_joins (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    inviter_id INTEGER NOT NULL,
    code TEXT,
    has_left INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS economy (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    added_by INTEGER,
    added_at INTEGER
);

CREATE TABLE IF NOT EXISTS marriages (
    user1 INTEGER NOT NULL,
    user2 INTEGER NOT NULL,
    married_at INTEGER,
    PRIMARY KEY (user1, user2)
);

CREATE TABLE IF NOT EXISTS stickies (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS link_allowed (
    guild_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, target_id)
);

CREATE TABLE IF NOT EXISTS quests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    prize TEXT NOT NULL,
    description TEXT,
    host_id INTEGER NOT NULL,
    winners INTEGER NOT NULL,
    end_time INTEGER NOT NULL,
    reroll_seconds INTEGER NOT NULL,
    req_invites INTEGER NOT NULL DEFAULT 0,
    req_role INTEGER,
    blocked_role INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    result_message_id INTEGER,
    claim_deadline INTEGER
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS giveaway_winners (
    giveaway_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (giveaway_id, user_id)
);
"""


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn is not None:
            await self.conn.close()

    async def execute(self, sql, params=()):
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor

    async def fetchone(self, sql, params=()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, sql, params=()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    # ---- settings -------------------------------------------------------
    async def get_setting(self, guild_id, column, default=None):
        if column not in SETTING_COLUMNS:
            raise ValueError(column)
        row = await self.fetchone(f"SELECT {column} FROM settings WHERE guild_id = ?", (guild_id,))
        if row is None or row[column] is None:
            return default
        return row[column]

    async def set_setting(self, guild_id, column, value):
        if column not in SETTING_COLUMNS:
            raise ValueError(column)
        await self.execute("INSERT OR IGNORE INTO settings (guild_id) VALUES (?)", (guild_id,))
        await self.execute(f"UPDATE settings SET {column} = ? WHERE guild_id = ?", (value, guild_id))

    # ---- economy (global: one balance per user across every server) -----
    async def get_balance(self, user_id):
        row = await self.fetchone("SELECT balance FROM economy WHERE user_id = ?", (user_id,))
        return row["balance"] if row else 0

    async def add_balance(self, user_id, amount):
        await self.execute("INSERT OR IGNORE INTO economy (user_id) VALUES (?)", (user_id,))
        await self.execute("UPDATE economy SET balance = balance + ? WHERE user_id = ?", (amount, user_id))

    async def take_balance(self, user_id, amount):
        """Removes tokens only if the user has enough. Returns True on success."""
        await self.execute("INSERT OR IGNORE INTO economy (user_id) VALUES (?)", (user_id,))
        cursor = await self.execute(
            "UPDATE economy SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        return cursor.rowcount > 0

    # ---- blacklist ------------------------------------------------------
    async def is_blacklisted(self, user_id):
        row = await self.fetchone("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
        return row is not None
