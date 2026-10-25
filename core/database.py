
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.config import DATABASE_PATH


def get_connection():
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def transaction():
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with transaction() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            age INTEGER,
            occupation TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            stamina INTEGER NOT NULL DEFAULT 100,
            max_stamina INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, id),
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id),
            CHECK (stamina >= 0),
            CHECK (max_stamina > 0)
        );

        CREATE INDEX IF NOT EXISTS idx_characters_owner
        ON characters(guild_id, owner_id);

        CREATE TABLE IF NOT EXISTS attributes (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, character_id, name),
            FOREIGN KEY (guild_id, character_id)
                REFERENCES characters(guild_id, id),
            CHECK (value >= 0)
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            account_type TEXT NOT NULL DEFAULT 'wallet',
            UNIQUE (guild_id, owner_type, owner_id, account_type),
            UNIQUE (guild_id, id),
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
        );

        CREATE TABLE IF NOT EXISTS ledger_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            unique_key TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, unique_key),
            UNIQUE (guild_id, id),
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
        );

        CREATE TABLE IF NOT EXISTS ledger_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            transaction_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            FOREIGN KEY (guild_id, transaction_id)
                REFERENCES ledger_transactions(guild_id, id),
            FOREIGN KEY (guild_id, account_id)
                REFERENCES accounts(guild_id, id)
        );

        CREATE TABLE IF NOT EXISTS module_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            module TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            record_type TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id)
        );
        """)


def register_guild(guild_id, name=""):
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO guilds (guild_id, name) VALUES (?, ?)",
            (str(guild_id), name),
        )


def create_character(guild_id, owner_id, name, age=None, occupation=""):
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO guilds (guild_id) VALUES (?)",
            (str(guild_id),),
        )
        cursor = conn.execute(
            """
            INSERT INTO characters
                (guild_id, owner_id, name, age, occupation)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(guild_id), str(owner_id), name, age, occupation),
        )
        return cursor.lastrowid


def get_characters(guild_id, owner_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM characters
            WHERE guild_id = ? AND owner_id = ?
            ORDER BY id
            """,
            (str(guild_id), str(owner_id)),
        ).fetchall()
        return [dict(row) for row in rows]


def get_attribute(guild_id, character_id, name):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT value FROM attributes
            WHERE guild_id = ? AND character_id = ? AND name = ?
            """,
            (str(guild_id), character_id, name),
        ).fetchone()
        return None if row is None else row["value"]


if __name__ == "__main__":
    init_db()
    print("資料庫初始化成功！")
  
