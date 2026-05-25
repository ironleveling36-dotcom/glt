"""
database.py — SQLite helpers for GTL Bot
Tables:
  - channels  : list of channels admin has added (force-subscribe targets)
  - users     : tracks who has verified membership
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "gtl_bot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  TEXT    NOT NULL UNIQUE,
                channel_name TEXT   NOT NULL,
                invite_link TEXT,
                added_by    INTEGER NOT NULL,
                added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL UNIQUE,
                username    TEXT,
                full_name   TEXT,
                is_verified INTEGER DEFAULT 0,
                first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)


# ── Channel helpers ──────────────────────────────────────────────────────────

def add_channel(channel_id: str, channel_name: str, invite_link: str, added_by: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO channels (channel_id, channel_name, invite_link, added_by) VALUES (?,?,?,?)",
            (channel_id, channel_name, invite_link, added_by),
        )


def remove_channel(channel_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))


def get_all_channels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()


def get_channel(channel_id: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()


# ── User helpers ─────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str, full_name: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users (user_id, username, full_name)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username  = excluded.username,
                 full_name = excluded.full_name,
                 last_seen = CURRENT_TIMESTAMP""",
            (user_id, username, full_name),
        )


def set_user_verified(user_id: int, verified: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_verified = ? WHERE user_id = ?",
            (1 if verified else 0, user_id),
        )


def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def get_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY first_seen DESC").fetchall()


def get_stats():
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        verified = conn.execute("SELECT COUNT(*) FROM users WHERE is_verified = 1").fetchone()[0]
        channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        return total, verified, channels
