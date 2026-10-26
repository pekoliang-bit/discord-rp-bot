
"""Discord RP 共用權限與角色雙重審核系統。

提供：
- Actor 操作者識別
- 管理員與角色擁有者驗證
- 私人角色檔案的檢視權限
- 角色送審
- 第一階段與第二階段審核
- 退件與審核紀錄

不註冊 Discord 斜線指令。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import discord

from core.database import (
    get_connection,
    transaction,
)


class PermissionDenied(Exception):
    """操作權限不足。"""


class InvalidState(Exception):
    """操作不符合目前資料狀態。"""


@dataclass(frozen=True)
class Actor:
    guild_id: str
    user_id: str
    is_admin: bool = False

    @classmethod
    def from_interaction(
        cls,
        interaction: discord.Interaction,
    ) -> "Actor":

        if interaction.guild_id is None:
            raise InvalidState(
                "此功能只能在 Discord 伺服器內使用。"
            )

        if interaction.user is None:
            raise PermissionDenied(
                "無法識別 Discord 操作者。"
            )

        permissions = interaction.permissions

        return cls(
            guild_id=str(interaction.guild_id),
            user_id=str(interaction.user.id),
            is_admin=bool(
                permissions.administrator
                or permissions.manage_guild
            ),
        )


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise PermissionDenied(
            "此功能僅限伺服器管理員使用。"
        )


def require_guild(
    actor: Actor,
    guild_id: str | int,
) -> None:

    if actor.guild_id != str(guild_id):
        raise PermissionDenied(
            "禁止跨伺服器操作資料。"
        )


def require_owner(
    actor: Actor,
    owner_id: str | int,
    *,
    allow_admin: bool = True,
) -> None:

    if str(owner_id) == actor.user_id:
        return

    if allow_admin and actor.is_admin:
        return

    raise PermissionDenied(
        "你沒有操作這名角色的權限。"
    )


def _get_character(
    conn,
    actor: Actor,
    character_id: int,
):
    if (
        type(character_id) is not int
        or character_id <= 0
    ):
        raise ValueError(
            "角色編號必須是正整數。"
        )

    character = conn.execute(
        """
        SELECT *
        FROM characters
        WHERE guild_id = ?
          AND id = ?
        """,
        (
            actor.guild_id,
            character_id,
        ),
    ).fetchone()

    if character is None:
        raise InvalidState(
            "找不到指定的角色。"
        )

    if character["status"] == "deleted":
        raise InvalidState(
            "角色已刪除。"
        )

    return character


def require_character_owner(
    actor: Actor,
    character_id: int,
):
    """角色送審前驗證，僅角色本人擁有者可操作。"""

    with get_connection() as conn:
        character = _get_character(
            conn,
            actor,
            character_id,
        )

        if str(character["owner_id"]) != actor.user_id:
            raise PermissionDenied(
                "只有角色擁有者可以執行此操作。"
            )

        if character["character_type"] != "player":
            raise PermissionDenied(
                "此操作僅適用於玩家角色。"
            )

        return character


def require_character_visible(
    actor: Actor,
    character_id: int,
):
    """完整檔案含私人欄位，目前僅擁有者及管理員可看。"""

    with get_connection() as conn:
        character = _get_character(
            conn,
            actor,
            character_id,
        )

        if (
            str(character["owner_id"]) != actor.user_id
            and not actor.is_admin
        ):
            raise PermissionDenied(
                "你沒有查看此角色完整檔案的權限。"
            )

        return character


def _init_reviews(conn):
    """保存雙重審核的審核者及回合。"""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS
        character_review_progress (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL DEFAULT 0,
            first_reviewer TEXT,
            first_reviewed_at TEXT,
            second_reviewer TEXT,
            second_reviewed_at TEXT,
            PRIMARY KEY (guild_id, character_id)
        )
        """
    )


def _audit(
    conn,
    actor: Actor,
    action: str,
    character_id: int,
    details: dict,
):
    conn.execute(
        """
        INSERT INTO audit_events (
            guild_id,
            actor_id,
            action,
            target_key,
            details_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            actor.guild_id,
            actor.user_id,
            action,
            f"character:{character_id}",
            json.dumps(
                details,
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


def submit_character(
    actor: Actor,
    character_id: int,
) -> str:
    """由草稿送交第一階段審核。"""

    with transaction() as conn:
        _init_reviews(conn)

        character = _get_character(
            conn,
            actor,
            character_id,
        )

        if str(character["owner_id"]) != actor.user_id:
            raise PermissionDenied(
                "只能送審自己的角色。"
            )

        if character["character_type"] != "player":
            raise PermissionDenied(
                "只有玩家角色可以使用此送審流程。"
            )

        if character["status"] != "draft":
            raise InvalidState(
                "只有草稿狀態的角色可以送審。"
            )

        changed = conn.execute(
            """
            UPDATE characters
            SET status = 'review_1',
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = 'draft'
            """,
            (
                actor.guild_id,
                character_id,
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "角色狀態已改變，請重新操作。"
            )

        conn.execute(
            """
            INSERT INTO character_review_progress (
                guild_id,
                character_id,
                round_number
            )
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id, character_id)
            DO UPDATE SET
                round_number = round_number + 1,
                first_reviewer = NULL,
                first_reviewed_at = NULL,
                second_reviewer = NULL,
                second_reviewed_at = NULL
            """,
            (
                actor.guild_id,
                character_id,
            ),
        )

        _audit(
            conn,
            actor,
            "character.submit",
            character_id,
            {
                "status": "review_1",
            },
        )

        return "待第一階段審核"


def review_character(
    actor: Actor,
    character_id: int,
    *,
    approve: bool,
    reason: str = "",
) -> str:
    """進行第一審、第二審或退件。"""

    require_admin(actor)

    reason = (reason or "").strip()

    if len(reason) > 500:
        raise ValueError(
            "審核意見不得超過500字。"
        )

    if not approve and not reason:
        raise ValueError(
            "退件時必須填寫原因。"
        )

    with transaction() as conn:
        _init_reviews(conn)

        character = _get_character(
            conn,
            actor,
            character_id,
        )

        if str(character["owner_id"]) == actor.user_id:
            raise PermissionDenied(
                "管理員不能審核自己的角色。"
            )

        if character["character_type"] != "player":
            raise InvalidState(
                "此流程僅適用於玩家角色。"
            )

        old_status = character["status"]

        if old_status not in (
            "review_1",
            "review_2",
        ):
            raise InvalidState(
                "這名角色目前不在待審核狀態。"
            )

        progress = conn.execute(
            """
            SELECT *
            FROM character_review_progress
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if progress is None:
            raise InvalidState(
                "找不到角色審核進度，請管理員檢查。"
            )

        if not approve:
            new_status = "draft"

        elif old_status == "review_1":
            new_status = "review_2"

        else:
            if not progress["first_reviewer"]:
                raise InvalidState(
                    "缺少第一階段審核紀錄。"
                )

            if str(progress["first_reviewer"]) == actor.user_id:
                raise PermissionDenied(
                    "第一審與第二審必須由不同管理員執行。"
                )

            new_status = "approved"

        changed = conn.execute(
            """
            UPDATE characters
            SET status = ?,
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = ?
            """,
            (
                new_status,
                actor.guild_id,
                character_id,
                old_status,
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "角色狀態已變更，請重新審核。"
            )

        if approve and old_status == "review_1":
            conn.execute(
                """
                UPDATE character_review_progress
                SET first_reviewer = ?,
                    first_reviewed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        elif approve:
            conn.execute(
                """
                UPDATE character_review_progress
                SET second_reviewer = ?,
                    second_reviewed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        _audit(
            conn,
            actor,
            "character.review",
            character_id,
            {
                "from": old_status,
                "to": new_status,
                "approved": approve,
                "reason": reason,
            },
        )

        descriptions = {
            "draft": "審核未通過，已退回草稿",
            "review_2": "第一審通過，等待第二審",
            "approved": "雙重審核通過，等待玩家確認啟用",
        }

        return descriptions[new_status]
