"""虛構網友留言履歷與防重複預約（不會自動發文）。

同一伺服器、事件、網友只有一份留言紀錄。先 reserve，再由上層
成功發布後 complete；失敗時 cancel。公開發文之前必須再完成
事件/模板授權驗證，且傳入穩定的 event_id。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.database import get_connection


DDL = """CREATE TABLE IF NOT EXISTS internet_comment_appearances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    character_id INTEGER NOT NULL CHECK(character_id > 0),
    virtual_user_id INTEGER NOT NULL CHECK(virtual_user_id > 0),
    template_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved'
        CHECK(status IN ('reserved','posted','cancelled')),
    created_at TEXT NOT NULL,
    posted_at TEXT,
    discord_message_id TEXT,
    UNIQUE(guild_id, event_id, virtual_user_id)
)"""
INDEX = """CREATE INDEX IF NOT EXISTS idx_internet_comment_recent
ON internet_comment_appearances(guild_id, character_id, status, posted_at)"""


@dataclass(frozen=True)
class Appearance:
    id: int
    guild_id: str
    event_id: str
    character_id: int
    virtual_user_id: int
    template_id: str
    status: str
    created_at: str
    posted_at: Optional[str]
    discord_message_id: Optional[str]


def _id(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} 必須是正整數")
    return value


def _name(value: object, label: str, max_len: int = 200) -> str:
    s = str(value).strip()
    if not s or len(s) > max_len:
        raise ValueError(f"{label} 不可為空，長度上限 {max_len}")
    return s


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize(conn: sqlite3.Connection) -> None:
    conn.execute(DDL)
    conn.execute(INDEX)


def _as_appearance(row: sqlite3.Row) -> Appearance:
    return Appearance(**dict(row))


def reserve_appearance(*, guild_id: str | int, event_id: str,
                       character_id: int, virtual_user_id: int,
                       template_id: str) -> tuple[Appearance, bool]:
    """原子預約；返回 (紀錄, 是否成功新預約)。

    不能將既有 posted 紀錄重新預約；cancelled 可原位重試。
    """
    gid = _name(guild_id, 'guild_id')
    eid = _name(event_id, 'event_id')
    cid = _id(character_id, 'character_id')
    uid = _id(virtual_user_id, 'virtual_user_id')
    tid = _name(template_id, 'template_id', 64)
    now = _utc_now()
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        initialize(conn)
        row = conn.execute(
            "SELECT * FROM internet_comment_appearances WHERE guild_id=? AND event_id=? AND virtual_user_id=?",
            (gid, eid, uid),
        ).fetchone()
        created = False
        if row is None:
            conn.execute(
                """INSERT INTO internet_comment_appearances
                (guild_id,event_id,character_id,virtual_user_id,template_id,status,created_at)
                VALUES (?,?,?,?,?,'reserved',?)""",
                (gid, eid, cid, uid, tid, now),
            )
            created = True
        elif row['status'] == 'cancelled' and row['character_id'] == cid:
            conn.execute(
                """UPDATE internet_comment_appearances SET status='reserved',
                template_id=?,created_at=?,posted_at=NULL,discord_message_id=NULL WHERE id=?""",
                (tid, now, row['id']),
            )
            created = True
        row = conn.execute(
            "SELECT * FROM internet_comment_appearances WHERE guild_id=? AND event_id=? AND virtual_user_id=?",
            (gid, eid, uid),
        ).fetchone()
        conn.commit()
        return _as_appearance(row), created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_appearance(*, guild_id: str | int, appearance_id: int,
                        discord_message_id: str) -> Appearance:
    """成功發布後標記 posted；重試同一訊息 ID 是冪等的。

    注意：Discord 發文與 SQLite 無法跨系統原子提交。遇到發文成功但
    回寫失敗，請先查原 Discord 訊息，避免重送。
    """
    gid = _name(guild_id, 'guild_id')
    aid = _id(appearance_id, 'appearance_id')
    mid = _name(discord_message_id, 'discord_message_id')
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        initialize(conn)
        row = conn.execute(
            'SELECT * FROM internet_comment_appearances WHERE guild_id=? AND id=?',
            (gid, aid),
        ).fetchone()
        if row is None:
            raise ValueError('找不到留言預約')
        if row['status'] == 'posted':
            if row['discord_message_id'] != mid:
                raise ValueError('該筆紀錄已對應另一個 Discord 訊息')
        elif row['status'] != 'reserved':
            raise ValueError('只有 reserved 狀態可以完成')
        else:
            conn.execute(
                """UPDATE internet_comment_appearances SET status='posted',
                posted_at=?, discord_message_id=? WHERE id=?""",
                (_utc_now(), mid, aid),
            )
        updated = conn.execute('SELECT * FROM internet_comment_appearances WHERE id=?', (aid,)).fetchone()
        conn.commit()
        return _as_appearance(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_appearance(*, guild_id: str | int, appearance_id: int) -> bool:
    """僅可取消 reserved。已發布的留言不允許偽裝成未發布。"""
    gid = _name(guild_id, 'guild_id')
    aid = _id(appearance_id, 'appearance_id')
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        initialize(conn)
        cursor = conn.execute(
            "UPDATE internet_comment_appearances SET status='cancelled' WHERE guild_id=? AND id=? AND status='reserved'",
            (gid, aid),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recent_posted_user_ids(*, guild_id: str | int, character_id: int,
                           event_limit: int = 3) -> frozenset[int]:
    """回傳最近 N 個已發布事件曾出現的虛構網友 ID。

    只計 posted；同事件多位網友也只算一個事件。
    """
    gid = _name(guild_id, 'guild_id')
    cid = _id(character_id, 'character_id')
    if type(event_limit) is not int or not 0 <= event_limit <= 100:
        raise ValueError('event_limit 必須為 0～100')
    if event_limit == 0:
        return frozenset()
    conn = get_connection()
    try:
        initialize(conn)
        events = conn.execute(
            """SELECT event_id, MAX(posted_at) AS last_post FROM internet_comment_appearances
            WHERE guild_id=? AND character_id=? AND status='posted'
            GROUP BY event_id ORDER BY last_post DESC, event_id DESC LIMIT ?""",
            (gid, cid, event_limit),
        ).fetchall()
        if not events:
            return frozenset()
        ids = [r['event_id'] for r in events]
        placeholders = ','.join('?' for _ in ids)
        rows = conn.execute(
            f"SELECT DISTINCT virtual_user_id FROM internet_comment_appearances WHERE guild_id=? AND character_id=? AND status='posted' AND event_id IN ({placeholders})",
            (gid, cid, *ids),
        ).fetchall()
        return frozenset(int(r[0]) for r in rows)
    finally:
        conn.close()
