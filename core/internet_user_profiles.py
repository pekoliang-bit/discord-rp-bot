
"""Discord RP 虛構網友身分資料庫。

用途：
- 建立持續存在的虛構網友
- 維護網名及網友類型
- 記錄網友與不同藝人的關係
- 支援固定粉絲、站姐及黑粉
- 保留網友跨事件出現的紀錄

不會自動發布留言。
不會增加玩家粉絲、金錢或影響指數。
不操作真實 Discord 使用者帳號。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.database import get_connection
from core.internet_username_generator import (
    ACCOUNT_TYPES,
    normalize_username,
)


RELATION_TYPES = frozenset({
    "neutral",
    "fan",
    "superfan",
    "fansite",
    "solo_fan",
    "anti",
    "cp_fan",
})

MAX_FAMILIARITY = 100
MAX_INFLUENCE = 100
MAX_APPEARANCE_WEIGHT = 100


DDL = (
    """
    CREATE TABLE IF NOT EXISTS
    internet_virtual_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        username TEXT NOT NULL,
        account_type TEXT NOT NULL,
        influence INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
            DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(guild_id, username),
        CHECK(influence BETWEEN 0 AND 100),
        CHECK(active IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS
    internet_virtual_user_relations (
        guild_id TEXT NOT NULL,
        virtual_user_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        relation_type TEXT NOT NULL DEFAULT 'neutral',
        familiarity INTEGER NOT NULL DEFAULT 0,
        appearance_weight INTEGER NOT NULL DEFAULT 20,
        last_appearance_at TEXT,
        appearance_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (
            guild_id,
            virtual_user_id,
            character_id
        ),
        FOREIGN KEY(virtual_user_id)
            REFERENCES internet_virtual_users(id)
            ON DELETE CASCADE,
        CHECK(familiarity BETWEEN 0 AND 100),
        CHECK(appearance_weight BETWEEN 0 AND 100),
        CHECK(appearance_count >= 0)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS
    idx_virtual_user_character
    ON internet_virtual_user_relations(
        guild_id,
        character_id,
        relation_type
    )
    """,
)


@dataclass(frozen=True)
class VirtualUser:
    id: int
    guild_id: str
    username: str
    account_type: str
    influence: int
    active: bool


@dataclass(frozen=True)
class VirtualUserRelation:
    guild_id: str
    virtual_user_id: int
    character_id: int
    relation_type: str
    familiarity: int
    appearance_weight: int
    last_appearance_at: Optional[str]
    appearance_count: int


def _guild_id(value: str | int) -> str:
    result = str(value).strip()

    if not result:
        raise ValueError(
            "guild_id 不可為空"
        )

    return result


def _positive_id(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(
            f"{label} 必須是正整數"
        )

    return value


def _bounded(
    value: int,
    label: str,
    maximum: int = 100,
) -> int:
    if (
        type(value) is not int
        or not 0 <= value <= maximum
    ):
        raise ValueError(
            f"{label} 必須是 0～{maximum}"
        )

    return value


def initialize(
    conn: sqlite3.Connection,
) -> None:
    """建立虛構網友專用資料表。

    僅由明確呼叫的服務執行；
    匯入本模組本身不會建立資料表。
    """

    for statement in DDL:
        conn.execute(statement)


def _user_from_row(
    row: sqlite3.Row,
) -> VirtualUser:
    return VirtualUser(
        id=int(row["id"]),
        guild_id=str(row["guild_id"]),
        username=str(row["username"]),
        account_type=str(row["account_type"]),
        influence=int(row["influence"]),
        active=bool(row["active"]),
    )


def create_virtual_user(
    guild_id: str | int,
    username: str,
    account_type: str = "passerby",
    *,
    influence: int = 0,
) -> VirtualUser:
    """明確建立虛構網友。

    不會新增 Discord 玩家。
    不會改變任何藝人的粉絲統計。
    """

    gid = _guild_id(guild_id)
    username = normalize_username(username)

    if account_type not in ACCOUNT_TYPES:
        raise ValueError(
            "不支援的網友類型"
        )

    influence = _bounded(
        influence,
        "影響力",
        MAX_INFLUENCE,
    )

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)

        cursor = conn.execute(
            """
            INSERT INTO internet_virtual_users (
                guild_id,
                username,
                account_type,
                influence
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                gid,
                username,
                account_type,
                influence,
            ),
        )

        row = conn.execute(
            """
            SELECT *
            FROM internet_virtual_users
            WHERE id = ?
              AND guild_id = ?
            """,
            (cursor.lastrowid, gid),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "虛構網友建立後無法讀取"
            )

        result = _user_from_row(row)
        conn.commit()
        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_virtual_user(
    guild_id: str | int,
    virtual_user_id: int,
) -> Optional[VirtualUser]:
    """依伺服器與帳號 ID 查詢虛構網友。"""

    gid = _guild_id(guild_id)
    uid = _positive_id(
        virtual_user_id,
        "虛構網友 ID",
    )

    conn = get_connection()

    try:
        initialize(conn)

        row = conn.execute(
            """
            SELECT *
            FROM internet_virtual_users
            WHERE guild_id = ?
              AND id = ?
            """,
            (gid, uid),
        ).fetchone()

        return (
            _user_from_row(row)
            if row is not None
            else None
        )

    finally:
        conn.close()


def set_character_relation(
    guild_id: str | int,
    virtual_user_id: int,
    character_id: int,
    relation_type: str,
    *,
    familiarity: int = 0,
    appearance_weight: int = 20,
) -> VirtualUserRelation:
    """建立或更新虛構網友與藝人的關係。

    不自動判定角色是否真實存在。
    呼叫者必須先在角色系統確認：
    角色屬於同一 guild，且允許被公開追蹤。

    appearance_weight 是未來供排程器使用的
    相對權重，不是百分比保證。
    """

    gid = _guild_id(guild_id)

    uid = _positive_id(
        virtual_user_id,
        "虛構網友 ID",
    )

    cid = _positive_id(
        character_id,
        "角色 ID",
    )

    if relation_type not in RELATION_TYPES:
        raise ValueError(
            "不支援的關係類型"
        )

    familiarity = _bounded(
        familiarity,
        "熟悉度",
        MAX_FAMILIARITY,
    )

    appearance_weight = _bounded(
        appearance_weight,
        "出現權重",
        MAX_APPEARANCE_WEIGHT,
    )

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)

        user = conn.execute(
            """
            SELECT id
            FROM internet_virtual_users
            WHERE guild_id = ?
              AND id = ?
              AND active = 1
            """,
            (gid, uid),
        ).fetchone()

        if user is None:
            raise ValueError(
                "虛構網友不存在或已停用"
            )

        conn.execute(
            """
            INSERT INTO internet_virtual_user_relations (
                guild_id,
                virtual_user_id,
                character_id,
                relation_type,
                familiarity,
                appearance_weight
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(
                guild_id,
                virtual_user_id,
                character_id
            )
            DO UPDATE SET
                relation_type = excluded.relation_type,
                familiarity = excluded.familiarity,
                appearance_weight =
                    excluded.appearance_weight
            """,
            (
                gid,
                uid,
                cid,
                relation_type,
                familiarity,
                appearance_weight,
            ),
        )

        row = conn.execute(
            """
            SELECT *
            FROM internet_virtual_user_relations
            WHERE guild_id = ?
              AND virtual_user_id = ?
              AND character_id = ?
            """,
            (gid, uid, cid),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "網友關係建立失敗"
            )

        result = VirtualUserRelation(
            guild_id=str(row["guild_id"]),
            virtual_user_id=int(
                row["virtual_user_id"]
            ),
            character_id=int(row["character_id"]),
            relation_type=str(row["relation_type"]),
            familiarity=int(row["familiarity"]),
            appearance_weight=int(
                row["appearance_weight"]
            ),
            last_appearance_at=row[
                "last_appearance_at"
            ],
            appearance_count=int(
                row["appearance_count"]
            ),
        )

        conn.commit()
        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def list_character_followers(
    guild_id: str | int,
    character_id: int,
    *,
    relation_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """取得某位藝人的固定虛構網友。

    黑粉等關係也是此處的「關注關係」，
    不代表增加既有粉絲數值。
    """

    gid = _guild_id(guild_id)
    cid = _positive_id(character_id, "角色 ID")

    if relation_type is not None:
        if relation_type not in RELATION_TYPES:
            raise ValueError("關係類型無效")

    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError(
            "limit 必須為 1～500"
        )

    conn = get_connection()

    try:
        initialize(conn)

        query = """
            SELECT
                u.id,
                u.username,
                u.account_type,
                u.influence,
                r.relation_type,
                r.familiarity,
                r.appearance_weight,
                r.last_appearance_at,
                r.appearance_count
            FROM internet_virtual_user_relations r
            JOIN internet_virtual_users u
              ON u.id = r.virtual_user_id
             AND u.guild_id = r.guild_id
            WHERE r.guild_id = ?
              AND r.character_id = ?
              AND u.active = 1
        """

        params: list = [gid, cid]

        if relation_type is not None:
            query += " AND r.relation_type = ?"
            params.append(relation_type)

        query += """
            ORDER BY
                r.appearance_weight DESC,
                r.familiarity DESC,
                u.id ASC
            LIMIT ?
        """

        params.append(limit)

        rows = conn.execute(
            query,
            params,
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        conn.close()


def record_appearance(
    guild_id: str | int,
    virtual_user_id: int,
    character_id: int,
) -> bool:
    """記錄已完成的模擬留言出現次數。

    應在真正的留言發布或儲存成功後呼叫。
    不能將預覽視為已出現。
    """

    gid = _guild_id(guild_id)
    uid = _positive_id(
        virtual_user_id,
        "虛構網友 ID",
    )
    cid = _positive_id(
        character_id,
        "角色 ID",
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    conn = get_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)

        cursor = conn.execute(
            """
            UPDATE internet_virtual_user_relations
            SET
                appearance_count =
                    appearance_count + 1,
                last_appearance_at = ?
            WHERE guild_id = ?
              AND virtual_user_id = ?
              AND character_id = ?
              AND EXISTS (
                  SELECT 1
                  FROM internet_virtual_users
                  WHERE id = ?
                    AND guild_id = ?
                    AND active = 1
              )
            """,
            (
                now,
                gid,
                uid,
                cid,
                uid,
                gid,
            ),
        )

        updated = cursor.rowcount > 0
        conn.commit()
        return updated

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
