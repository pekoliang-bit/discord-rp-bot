
"""Discord RP：群主專屬角色審核特權。

規則：
1. Discord 伺服器實際擁有者可以審核自己的角色。
2. 群主可以親自完成第一審及第二審。
3. 其他授權審核員不能審核自己的角色。
4. 其他審核員的第一審、第二審必須是不同人。
5. Administrator 權限不等於群主。
6. 其他角色與金融審核規則保持不變。

不新增資料表，不刪除原審核紀錄。
"""

from __future__ import annotations

import json
import logging

import discord
from discord.ext import commands

from core import permissions
from core.database import transaction

from modules import profile
from modules import review_center

LOG = logging.getLogger(__name__)

# 保存原來的審核函式。
# 非群主繼續使用原有審核邏輯。
ORIGINAL_CORE_REVIEW = (
    permissions.review_character
)

ORIGINAL_CENTER_REVIEW = (
    review_center.approve_character
)

# setup 時設定。
BOT = None


def is_real_guild_owner(
    guild_id: str,
    user_id: str,
) -> bool:
    """只信任 Discord 實際伺服器擁有者。

    不能用 is_admin 或 Administrator
    來代替群主身分。

    無法取得伺服器資料時，不授予特權。
    """

    if BOT is None:
        return False

    try:
        guild = BOT.get_guild(
            int(guild_id)
        )

        if guild is None:
            LOG.warning(
                "無法取得伺服器 %s，"
                "拒絕使用群主審核特權。",
                guild_id,
            )
            return False

        return (
            str(guild.owner_id)
            == str(user_id)
        )

    except (TypeError, ValueError):
        return False


def review_as_owner(
    actor,
    character_id: int,
    *,
    approve: bool,
    reason: str = "",
) -> str:
    """群主專用角色雙階段審核。

    所有變更在單一交易內完成。
    同時記錄第一審、第二審的執行者。
    """

    if not is_real_guild_owner(
        actor.guild_id,
        actor.user_id,
    ):
        raise permissions.PermissionDenied(
            "只有伺服器實際擁有者"
            "可以使用此審核特權。"
        )

    if (
        type(character_id) is not int
        or character_id <= 0
    ):
        raise ValueError(
            "角色編號必須是正整數。"
        )

    reason = (
        reason or ""
    ).strip()

    if len(reason) > 500:
        raise ValueError(
            "審核意見不能超過500字。"
        )

    if not approve and not reason:
        raise ValueError(
            "退件時必須填寫理由。"
        )

    with transaction() as conn:
        # 沿用核心既有審核進度表。
        permissions._init_reviews(
            conn
        )

        character = conn.execute(
            """
            SELECT id,
                   owner_id,
                   status,
                   character_type
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
            raise permissions.InvalidState(
                "找不到指定角色。"
            )

        if (
            character["character_type"]
            != "player"
        ):
            raise permissions.InvalidState(
                "此審核流程只適用於玩家角色。"
            )

        old_status = character[
            "status"
        ]

        if old_status not in (
            "review_1",
            "review_2",
        ):
            raise permissions.InvalidState(
                "這名角色目前不在待審核狀態。"
            )

        progress = conn.execute(
            """
            SELECT first_reviewer,
                   second_reviewer
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
            raise permissions.InvalidState(
                "找不到角色審核進度紀錄。"
            )

        if not approve:
            new_status = "draft"

        elif old_status == "review_1":
            new_status = "review_2"

        else:
            if not progress[
                "first_reviewer"
            ]:
                raise permissions.InvalidState(
                    "缺少第一審紀錄。"
                )

            # 群主可以是第一、第二審同一人。
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
            raise permissions.InvalidState(
                "角色狀態已變更，"
                "請重新開啟審核介面。"
            )

        if approve and old_status == "review_1":
            conn.execute(
                """
                UPDATE character_review_progress
                SET first_reviewer = ?,
                    first_reviewed_at =
                        CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        elif approve and old_status == "review_2":
            conn.execute(
                """
                UPDATE character_review_progress
                SET second_reviewer = ?,
                    second_reviewed_at =
                        CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        # 沿用原有稽核表。
        # 額外標記這次是否使用群主特權。
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
                "character.review",
                f"character:{character_id}",
                json.dumps(
                    {
                        "from": old_status,
                        "to": new_status,
                        "approved": approve,
                        "reason": reason,
                        "guild_owner_override": True,
                        "self_review": (
                            str(
                                character["owner_id"]
                            )
                            == actor.user_id
                        ),
                        "same_reviewer": (
                            old_status == "review_2"
                            and str(
                                progress[
                                    "first_reviewer"
                                ]
                            ) == actor.user_id
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    descriptions = {
        "draft": (
            "審核未通過，已退回草稿"
        ),
        "review_2": (
            "群主第一審通過，"
            "等待第二審"
        ),
        "approved": (
            "群主雙重審核通過，"
            "等待玩家確認啟用"
        ),
    }

    LOG.info(
        "群主角色審核："
        "guild=%s actor=%s "
        "character=%s "
        "status=%s->%s",
        actor.guild_id,
        actor.user_id,
        character_id,
        old_status,
        new_status,
    )

    return descriptions[new_status]


def patched_core_review(
    actor,
    character_id,
    *,
    approve,
    reason="",
):
    """核心審核入口。

    群主使用專屬規則；
    其他管理員保留原有嚴格限制。
    """

    if is_real_guild_owner(
        actor.guild_id,
        actor.user_id,
    ):
        return review_as_owner(
            actor,
            character_id,
            approve=approve,
            reason=reason,
        )

    return ORIGINAL_CORE_REVIEW(
        actor,
        character_id,
        approve=approve,
        reason=reason,
    )


def patched_center_review(
    actor,
    number,
    approve,
    reason,
):
    """統一 /審核 中心專用入口。"""

    if is_real_guild_owner(
        actor.guild_id,
        actor.user_id,
    ):
        result = review_as_owner(
            actor,
            number,
            approve=approve,
            reason=reason,
        )

        LOG.info(
            "群主透過 /審核 完成角色審核：%s",
            result,
        )

        return result

    # 其他職能審核員：
    # 保留原本的禁止自審與雙人審核規則。
    return ORIGINAL_CENTER_REVIEW(
        actor,
        number,
        approve,
        reason,
    )


async def setup(
    bot: commands.Bot,
):
    global BOT

    BOT = bot

    # 更新核心函式，處理其他呼叫核心的入口。
    permissions.review_character = (
        patched_core_review
    )

    # profile.py 原先使用
    # from core.permissions import review_character
    # 因此也必須更新已匯入的引用。
    profile.review_character = (
        patched_core_review
    )

    # 更新正式 /審核 中心所呼叫的函式。
    review_center.approve_character = (
        patched_center_review
    )

    LOG.info(
        "已啟用角色雙審權限規則："
        "Discord 群主可自行完成兩審；"
        "其他審核員禁止自審，"
        "且一、二審必須不同人。"
    )
