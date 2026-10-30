
"""Discord RP 模擬網友留言審核橋接。

用途：
- 對接既有 /審核 系統的核准結果
- 驗證案件、頻道、角色與審核員
- 將審核結果轉換為發文授權
- 防止跨伺服器或跨事件共用核准紀錄

此檔不是 Cog，不會註冊新的斜線指令。

重要：
審核資料必須來自可信的正式審核服務，
不可直接使用玩家自行提交的 Discord 指令參數。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from core.internet_comment_publish_guard import (
    PublishPermission,
)


@dataclass(frozen=True)
class CommentReviewRecord:
    approval_id: str

    guild_id: int
    event_id: str
    character_id: int
    channel_id: int

    reviewer_id: int
    applicant_id: int

    status: str

    reviewed_at: datetime

    # 審核紀錄必須明確屬於模擬留言。
    application_type: str = "internet_comment"

    # 是否為現有審核系統已確認的正式核准。
    verified_by_review_system: bool = False


@dataclass(frozen=True)
class CommentPublishApproval:
    approval_id: str
    guild_id: int
    event_id: str
    character_id: int
    channel_id: int
    reviewer_id: int
    approved_at: datetime

    permission: PublishPermission


def _positive_int(
    value: int,
    name: str,
) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(
            f"{name} 必須是正整數"
        )


def validate_review_record(
    record: CommentReviewRecord,
    *,
    expected_guild_id: int,
    expected_event_id: str,
    expected_character_id: int,
    expected_channel_id: int,
    reviewer_is_authorized: bool,
    reviewer_is_owner: bool = False,
) -> CommentPublishApproval:
    """驗證從正式審核系統取得的結果。

    權限規則：
    - 群主可以自行審核。
    - 其他審核員不得審核自己的申請。
    - 一般審核員必須具備審核權限。

    上層必須確認 reviewer_is_owner 與
    reviewer_is_authorized 的真實身分來源。
    """

    for name in (
        "guild_id",
        "character_id",
        "channel_id",
        "reviewer_id",
        "applicant_id",
    ):
        _positive_int(
            getattr(record, name),
            name,
        )

    for name, value in (
        ("expected_guild_id", expected_guild_id),
        (
            "expected_character_id",
            expected_character_id,
        ),
        ("expected_channel_id", expected_channel_id),
    ):
        _positive_int(value, name)

    if not record.approval_id.strip():
        raise ValueError(
            "缺少核准紀錄 ID"
        )

    if not expected_event_id.strip():
        raise ValueError(
            "缺少預期事件 ID"
        )

    if record.application_type != "internet_comment":
        raise PermissionError(
            "審核類型不是虛構網友留言"
        )

    if record.status != "approved":
        raise PermissionError(
            "留言申請尚未通過審核"
        )

    if not record.verified_by_review_system:
        raise PermissionError(
            "核准紀錄尚未經正式系統驗證"
        )

    if record.guild_id != expected_guild_id:
        raise PermissionError(
            "不能跨伺服器使用核准紀錄"
        )

    if record.event_id != expected_event_id:
        raise PermissionError(
            "核准事件不一致"
        )

    if record.character_id != expected_character_id:
        raise PermissionError(
            "核准角色不一致"
        )

    if record.channel_id != expected_channel_id:
        raise PermissionError(
            "核准頻道不一致"
        )

    if not (
        reviewer_is_authorized
        or reviewer_is_owner
    ):
        raise PermissionError(
            "核准者沒有審核權限"
        )

    if (
        record.reviewer_id == record.applicant_id
        and not reviewer_is_owner
    ):
        raise PermissionError(
            "非群主不得審核自己的申請"
        )

    reviewed_at = record.reviewed_at

    if reviewed_at.tzinfo is None:
        raise ValueError(
            "審核時間必須包含時區"
        )

    if reviewed_at > datetime.now(timezone.utc):
        raise ValueError(
            "核准時間不能晚於目前時間"
        )

    permission = PublishPermission(
        guild_id=record.guild_id,
        channel_id=record.channel_id,
        character_id=record.character_id,
        event_authorized=True,
        posting_authorized=True,
        simulated_comments_enabled=True,
    )

    return CommentPublishApproval(
        approval_id=record.approval_id,
        guild_id=record.guild_id,
        event_id=record.event_id,
        character_id=record.character_id,
        channel_id=record.channel_id,
        reviewer_id=record.reviewer_id,
        approved_at=reviewed_at,
        permission=permission,
    )


async def resolve_comment_approval(
    *,
    approval_id: str,
    guild_id: int,
    event_id: str,
    character_id: int,
    channel_id: int,
    load_review: Callable[
        [str, int],
        Awaitable[CommentReviewRecord | None],
    ],
    check_reviewer_permission: Callable[
        [int, int],
        Awaitable[bool],
    ],
    check_guild_owner: Callable[
        [int, int],
        Awaitable[bool],
    ],
) -> CommentPublishApproval:
    """從正式審核系統取得並驗證核准紀錄。

    三個 Callable 必須由機器人內部的可信
    審核服務實作，不能由玩家自行提供。

    每次正式發文前都應重新取得審核狀態，
    以免撤銷後仍使用舊授權。
    """

    if not approval_id.strip():
        raise ValueError(
            "approval_id 不可為空"
        )

    record = await load_review(
        approval_id,
        guild_id,
    )

    if record is None:
        raise LookupError(
            "找不到指定的審核紀錄"
        )

    is_authorized = await check_reviewer_permission(
        guild_id,
        record.reviewer_id,
    )

    is_owner = await check_guild_owner(
        guild_id,
        record.reviewer_id,
    )

    return validate_review_record(
        record,
        expected_guild_id=guild_id,
        expected_event_id=event_id,
        expected_character_id=character_id,
        expected_channel_id=channel_id,
        reviewer_is_authorized=is_authorized,
        reviewer_is_owner=is_owner,
    )
