
"""Discord RP 虛構網友評論發布服務。

本檔不是 Discord Cog，不會建立斜線指令。
不會自行啟動排程，也不會自動發布訊息。

正式發文必須由已通過權限檢查的上層服務呼叫。

依賴：
- core.internet_comment_publish_guard
- core.internet_comment_history
- discord.py

注意：
Discord 發文與 SQLite 交易無法做到跨系統原子提交。
若發文結果不明，禁止自動重送，以免重複留言。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

import discord

from core.internet_comment_publish_guard import (
    CheckedComment,
)
from core.internet_comment_history import (
    reserve_appearance,
    complete_appearance,
    cancel_appearance,
)


@dataclass(frozen=True)
class PublishResult:
    success: bool
    status: str

    event_id: str
    virtual_user_id: int

    message_id: int | None = None
    appearance_id: int | None = None

    detail: str = ""


def _verify_channel(
    channel,
    comment: CheckedComment,
) -> None:
    """確認目的地與經核准資訊一致。"""

    channel_id = getattr(
        channel,
        "id",
        None,
    )

    if channel_id != comment.channel_id:
        raise PermissionError(
            "目標頻道與核准頻道不符"
        )

    guild = getattr(
        channel,
        "guild",
        None,
    )

    if guild is None:
        raise PermissionError(
            "不允許將模擬評論發送至私人訊息"
        )

    if guild.id != comment.guild_id:
        raise PermissionError(
            "目標伺服器與核准伺服器不符"
        )

    if not callable(
        getattr(channel, "send", None)
    ):
        raise ValueError(
            "此頻道不支援發送訊息"
        )


async def publish_comment(
    *,
    channel,
    comment: CheckedComment,
    revalidate: Callable[
        [],
        Awaitable[CheckedComment],
    ],
) -> PublishResult:
    """發布一則已核准的虛構網友留言。

    revalidate 必須重新：
    - 讀取正式事件及授權狀態
    - 查核有效的虛構網友關係
    - 驗證評論模板與已渲染內容
    - 呼叫第 34 個檔案的安全檢查器

    不得直接以 lambda 回傳未驗證的舊預覽，
    也不得把 Discord 使用者輸入視為授權結果。

    此函式只會發布一則留言。
    """

    if not callable(revalidate):
        raise TypeError(
            "必須提供正式重新驗證函式"
        )

    _verify_channel(
        channel,
        comment,
    )

    fresh = await revalidate()

    if not isinstance(
        fresh,
        CheckedComment,
    ):
        raise TypeError(
            "重新驗證必須回傳 CheckedComment"
        )

    if fresh != comment:
        raise PermissionError(
            "留言或授權資訊已改變，"
            "取消本次發布"
        )

    # 先預約，防止同一事件同一網友重複發布。
    appearance, created = reserve_appearance(
        guild_id=str(comment.guild_id),
        event_id=comment.event_id,
        character_id=comment.character_id,
        virtual_user_id=comment.virtual_user_id,
        template_id=comment.comment_id,
    )

    if not created:
        return PublishResult(
            success=False,
            status="duplicate_or_pending",
            event_id=comment.event_id,
            virtual_user_id=comment.virtual_user_id,
            appearance_id=appearance.id,
            detail=(
                "該網友在同一事件已有發布紀錄"
                "或尚有未完成的預約"
            ),
        )

    # 僅在訊息送達前可明確確認失敗時取消預約。
    # 發文 API 發生逾時等不明狀態時，
    # 保留 reserved 給管理員核對 Discord 後處理。

    try:
        message = await channel.send(
            comment.content,
            allowed_mentions=discord.AllowedMentions.none(),
            suppress_embeds=True,
        )

    except discord.Forbidden:
        # 明確遭到 Discord 權限拒絕。
        cancel_appearance(
            guild_id=str(comment.guild_id),
            appearance_id=appearance.id,
        )

        return PublishResult(
            success=False,
            status="forbidden",
            event_id=comment.event_id,
            virtual_user_id=comment.virtual_user_id,
            appearance_id=appearance.id,
            detail="Discord 拒絕發送訊息",
        )

    except Exception:
        # HTTPException、網路中斷或其他不確定結果，
        # 都不能認定 Discord 一定沒有收到訊息。
        # 保留預約，不自動重試。
        raise

    # Discord 已回傳成功建立的訊息。
    # 如果 SQLite 回寫失敗，保留預約，
    # 由人工使用 Discord 訊息 ID 復原。
    completed = complete_appearance(
        guild_id=str(comment.guild_id),
        appearance_id=appearance.id,
        discord_message_id=str(message.id),
    )

    if completed.status != "posted":
        raise RuntimeError(
            "Discord 發布成功，"
            "但留言紀錄狀態異常，"
            "請人工核對"
        )

    return PublishResult(
        success=True,
        status="posted",
        event_id=comment.event_id,
        virtual_user_id=comment.virtual_user_id,
        message_id=message.id,
        appearance_id=appearance.id,
        detail="虛構網友留言已發布",
    )
