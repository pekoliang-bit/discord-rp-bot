
"""Discord RP 模擬網友留言失敗復原服務。

與 core.internet_comment_history 搭配使用。

用途：
- 唯讀列出尚未完成的留言預約
- 核對 Discord 原始訊息後補登 posted
- 明確確認未發文時，允許取消預約
- 保存人工復原的稽核紀錄

本模組不呼叫 Discord API，也不自動重發訊息。
所有授權及 Discord 訊息查證由可信上層提供。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from core.database import get_connection
from core.internet_comment_history import (
    Appearance,
    complete_appearance,
    cancel_appearance,
)


AUDIT_SQL = """
CREATE TABLE IF NOT EXISTS
internet_comment_recovery_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    appearance_id INTEGER NOT NULL,
    reviewer_id TEXT NOT NULL,
    action TEXT NOT NULL,
    discord_message_id TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(action IN ('confirm_posted', 'cancel_unsent'))
)
"""


@dataclass(frozen=True)
class RecoveryRequest:
    guild_id: str
    appearance_id: int
    reviewer_id: str
    action: str
    reason: str
    discord_message_id: str | None = None


def _validate(
    request: RecoveryRequest,
) -> None:
    if not request.guild_id.strip():
        raise ValueError("guild_id 不可為空")

    if (
        type(request.appearance_id) is not int
        or request.appearance_id <= 0
    ):
        raise ValueError("appearance_id 必須為正整數")

    if not request.reviewer_id.strip():
        raise ValueError("reviewer_id 不可為空")

    if request.action not in {
        "confirm_posted",
        "cancel_unsent",
    }:
        raise ValueError("未知復原操作")

    if len(request.reason.strip()) < 3:
        raise ValueError("復原理由至少三個字元")

    if request.action == "confirm_posted":
        if not (
            request.discord_message_id
            and request.discord_message_id.isdigit()
            and int(request.discord_message_id) > 0
        ):
            raise ValueError(
                "確認已發文時須提供 Discord 訊息 ID"
            )
    elif request.discord_message_id is not None:
        raise ValueError(
            "取消未發送預約時不能提供訊息 ID"
        )


def list_pending_appearances(
    guild_id: str | int,
    *,
    limit: int = 100,
) -> tuple[Appearance, ...]:
    """唯讀取得尚未完成的預約。

    須由上層先驗證查詢者有管理權限。
    """

    gid = str(guild_id).strip()

    if not gid:
        raise ValueError("guild_id 不可為空")

    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit 必須為 1～500")

    db = get_connection()

    try:
        existing = db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table'
              AND name='internet_comment_appearances'
            """
        ).fetchone()

        if existing is None:
            return ()

        db.row_factory = sqlite3.Row

        rows = db.execute(
            """
            SELECT *
            FROM internet_comment_appearances
            WHERE guild_id = ?
              AND status = 'reserved'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (gid, limit),
        ).fetchall()

        return tuple(
            Appearance(**dict(row))
            for row in rows
        )

    finally:
        db.close()


def _read_appearance(
    guild_id: str,
    appearance_id: int,
) -> Appearance:
    db = get_connection()

    try:
        db.row_factory = sqlite3.Row

        row = db.execute(
            """
            SELECT *
            FROM internet_comment_appearances
            WHERE guild_id = ?
              AND id = ?
            """,
            (guild_id, appearance_id),
        ).fetchone()

        if row is None:
            raise LookupError("找不到留言預約")

        return Appearance(**dict(row))

    finally:
        db.close()


def _write_audit(
    request: RecoveryRequest,
) -> None:
    """紀錄已成功執行的人工復原。"""

    db = get_connection()

    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(AUDIT_SQL)

        existing = db.execute(
            """
            SELECT id
            FROM internet_comment_recovery_audit
            WHERE guild_id = ?
              AND appearance_id = ?
              AND action = ?
              AND COALESCE(discord_message_id, '')
                  = COALESCE(?, '')
            LIMIT 1
            """,
            (
                request.guild_id,
                request.appearance_id,
                request.action,
                request.discord_message_id,
            ),
        ).fetchone()

        if existing is None:
            db.execute(
                """
                INSERT INTO internet_comment_recovery_audit (
                    guild_id,
                    appearance_id,
                    reviewer_id,
                    action,
                    discord_message_id,
                    reason,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.guild_id,
                    request.appearance_id,
                    request.reviewer_id,
                    request.action,
                    request.discord_message_id,
                    request.reason,
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                ),
            )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def reconcile_appearance(
    request: RecoveryRequest,
    *,
    reviewer_authorized: bool = False,
    discord_message_confirmed: bool = False,
    absence_confirmed: bool = False,
) -> Appearance:
    """人工復原一筆留言預約。

    安全條件：
    - reviewer_authorized 必須由 Discord 權限系統確認
    - discord_message_confirmed 必須查過原頻道與訊息
    - absence_confirmed 必須有可信的未發送證據

    注意：
    布林值本身不是身分驗證。
    不可讓一般玩家直接控制這些參數。
    """

    _validate(request)

    if not reviewer_authorized:
        raise PermissionError("審核員未授權")

    before = _read_appearance(
        request.guild_id,
        request.appearance_id,
    )

    if request.action == "confirm_posted":
        if not discord_message_confirmed:
            raise PermissionError(
                "尚未核對 Discord 原始訊息"
            )

        if before.status == "posted":
            if (
                before.discord_message_id
                != request.discord_message_id
            ):
                raise ValueError(
                    "已登記的 Discord 訊息 ID 不一致"
                )
        elif before.status == "reserved":
            complete_appearance(
                guild_id=request.guild_id,
                appearance_id=request.appearance_id,
                discord_message_id=(
                    request.discord_message_id or ""
                ),
            )
        else:
            raise ValueError(
                "已取消的預約不能確認為已發布"
            )

    elif request.action == "cancel_unsent":
        if not absence_confirmed:
            raise PermissionError(
                "未能確認留言未發送，禁止取消"
            )

        if before.status != "reserved":
            raise ValueError(
                "只有 reserved 可以取消"
            )

        changed = cancel_appearance(
            guild_id=request.guild_id,
            appearance_id=request.appearance_id,
        )

        if not changed:
            raise RuntimeError(
                "預約狀態已變動，請重新確認"
            )

    result = _read_appearance(
        request.guild_id,
        request.appearance_id,
    )

    # 只有在狀態更新成功後才記錄成功審核。
    # 若稽核寫入失敗，請保留預約與 Discord
    # 原始訊息資訊，交由管理員補登；
    # 不得因此再次發布相同留言。
    _write_audit(request)

    return result
