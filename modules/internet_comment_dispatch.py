
"""Discord RP 模擬網友留言整合調度器。

依賴：
- modules.internet_comment_bridge
- modules.internet_comment_preview
- modules.internet_comment_review_bridge
- modules.internet_comment_publisher
- core.internet_comment_publish_guard

所有 load_* 回呼必須由可信的伺服器服務提供。
本模組不能直接接受玩家提供的核准資料。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from core.internet_comment_publish_guard import (
    CheckedComment,
    check_comment_before_publish,
)
from modules.internet_comment_bridge import (
    CommentEvent,
    preview_by_id,
)
from modules.internet_comment_preview import (
    NamedCommentPreview,
)
from modules.internet_comment_review_bridge import (
    CommentPublishApproval,
)


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    guild_id: int
    character_id: int
    channel_id: int
    event_id: str
    approval_id: str
    virtual_user_id: int
    comment_id: str


@dataclass(frozen=True, slots=True)
class DispatchServices:
    """由機器人內部可信模組注入的服務。

    load_approval 必須查詢當下仍有效的正式審核紀錄，
    包含管理員權限與申請核准狀態。

    load_event 必須重新執行評論事件白名單檢查。

    load_user 必須根據 guild_id 與 character_id
    讀取當前有效的虛構網友關係。

    load_preview 必須從核准的評論庫重新渲染，
    並且不得使用任何玩家提交的任意文字。
    """

    load_approval: Callable[
        [DispatchRequest],
        Awaitable[CommentPublishApproval],
    ]
    load_event: Callable[
        [DispatchRequest],
        Awaitable[CommentEvent],
    ]
    load_user: Callable[
        [DispatchRequest],
        Awaitable[Mapping],
    ]
    load_preview: Callable[
        [CommentEvent, str],
        Awaitable[object],
    ]


def _positive(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} 必須是正整數")


def validate_request(request: DispatchRequest) -> None:
    for name in (
        "guild_id",
        "character_id",
        "channel_id",
        "virtual_user_id",
    ):
        _positive(getattr(request, name), name)

    for name in (
        "event_id",
        "approval_id",
        "comment_id",
    ):
        value = getattr(request, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不可為空")


async def prepare_checked_comment(
    request: DispatchRequest,
    services: DispatchServices,
) -> CheckedComment:
    """重新確認資料並產出可發文的 CheckedComment。

    僅準備與驗證；不發文、不寫 SQLite。
    """

    validate_request(request)

    approval = await services.load_approval(request)

    if not isinstance(approval, CommentPublishApproval):
        raise TypeError("核准服務返回不正確的資料型別")

    if (
        approval.approval_id != request.approval_id
        or approval.guild_id != request.guild_id
        or approval.character_id != request.character_id
        or approval.channel_id != request.channel_id
        or approval.event_id != request.event_id
    ):
        raise PermissionError("核准紀錄與本次發文不一致")

    permission = approval.permission

    if (
        permission.guild_id != request.guild_id
        or permission.channel_id != request.channel_id
        or permission.character_id != request.character_id
    ):
        raise PermissionError("核准頻道或角色不一致")

    event = await services.load_event(request)

    if not isinstance(event, CommentEvent):
        raise TypeError("事件服務返回不正確的資料型別")

    if event.event_id != request.event_id:
        raise PermissionError("事件 ID 不一致")

    if not event.verified:
        raise PermissionError("事件尚未核實")

    if event.speaker_authorized:
        raise PermissionError("虛構網友禁止使用官方身分")

    if request.comment_id not in event.permitted_comment_ids:
        raise PermissionError("評論未獲本次事件核准")

    user = await services.load_user(request)

    if not isinstance(user, Mapping):
        raise TypeError("固定網友查詢結果格式錯誤")

    if str(user.get("guild_id")) != str(request.guild_id):
        raise PermissionError("虛構網友不屬於本伺服器")

    if user.get("character_id") != request.character_id:
        raise PermissionError("網友關係不屬於指定角色")

    if user.get("id") != request.virtual_user_id:
        raise PermissionError("虛構網友 ID 不一致")

    if not bool(user.get("active", False)):
        raise PermissionError("虛構網友已停用")

    username = str(user.get("username", "")).strip()

    if not username:
        raise ValueError("網友名稱不可為空")

    # 預覽文字必須從正式評論庫重新渲染。
    template = await services.load_preview(
        event,
        request.comment_id,
    )

    if template is None:
        raise PermissionError("評論模板不可用")

    if (
        getattr(template, "event_id", None) != request.event_id
        or getattr(template, "comment_id", None) != request.comment_id
    ):
        raise ValueError("評論模板與事件不一致")

    rendered = getattr(template, "rendered", None)

    if not isinstance(rendered, str) or not rendered.strip():
        raise ValueError("評論渲染結果無效")

    named = NamedCommentPreview(
        event_id=request.event_id,
        virtual_user_id=request.virtual_user_id,
        username=username,
        relation_type=str(user.get("relation_type", "neutral")),
        comment_id=request.comment_id,
        rendered=rendered,
        emotion=str(getattr(template, "emotion", "")),
        speaker=str(getattr(template, "speaker", "")),
    )

    return check_comment_before_publish(
        permission=permission,
        event=event,
        preview=named,
        user_active=True,
        verified_template_id=request.comment_id,
        verified_rendered_text=rendered,
    )


async def publish_approved_comment(
    *,
    channel,
    request: DispatchRequest,
    services: DispatchServices,
    publisher=None,
):
    """在同一個可信服務中完成驗證與發布。

    publisher 預設為第 35 個檔案提供的函式。
    測試時可注入不會真的發文的假發布器。

    發文前再次驗證，防止核准紀錄中途被撤銷。
    """

    prepared = await prepare_checked_comment(
        request,
        services,
    )

    if publisher is None:
        from modules.internet_comment_publisher import (
            publish_comment,
        )
        publisher = publish_comment

    async def revalidate() -> CheckedComment:
        return await prepare_checked_comment(
            request,
            services,
        )

    return await publisher(
        channel=channel,
        comment=prepared,
        revalidate=revalidate,
    )
