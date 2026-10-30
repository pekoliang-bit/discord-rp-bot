
"""Discord RP 虛構網友留言整合調度器。

第 38 個檔案完整修正版。

流程：
正式審核 → 事件白名單 → 固定網友身分
→ 模板與發言者配對 → 安全檢查 → 發布服務

不註冊 Discord 指令。
不建立排程。
不主動發布訊息或修改資料庫。
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
    CommentPreview,
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
    """由可信的內部服務提供所有資料來源。"""

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
        Awaitable[CommentPreview],
    ]


def validate_request(
    request: DispatchRequest,
) -> None:
    if not isinstance(request, DispatchRequest):
        raise TypeError(
            "request 必須是 DispatchRequest"
        )

    for name in (
        "guild_id",
        "character_id",
        "channel_id",
        "virtual_user_id",
    ):
        value = getattr(request, name)

        if type(value) is not int or value <= 0:
            raise ValueError(
                f"{name} 必須為正整數"
            )

    for name in (
        "event_id",
        "approval_id",
        "comment_id",
    ):
        value = getattr(request, name)

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise ValueError(
                f"{name} 不可為空"
            )


def _check_approval(
    request: DispatchRequest,
    approval: CommentPublishApproval,
) -> None:
    if not isinstance(
        approval,
        CommentPublishApproval,
    ):
        raise TypeError(
            "審核服務返回無效資料"
        )

    if (
        approval.approval_id != request.approval_id
        or approval.guild_id != request.guild_id
        or approval.character_id != request.character_id
        or approval.channel_id != request.channel_id
        or approval.event_id != request.event_id
    ):
        raise PermissionError(
            "審核紀錄與本次發文請求不一致"
        )

    permission = approval.permission

    if (
        permission.guild_id != request.guild_id
        or permission.character_id != request.character_id
        or permission.channel_id != request.channel_id
    ):
        raise PermissionError(
            "審核權限與目標伺服器、頻道或角色不符"
        )


def _check_event(
    request: DispatchRequest,
    event: CommentEvent,
) -> None:
    if not isinstance(event, CommentEvent):
        raise TypeError(
            "事件服務返回無效資料"
        )

    if event.event_id != request.event_id:
        raise PermissionError(
            "事件識別碼不一致"
        )

    if not event.verified:
        raise PermissionError(
            "事件尚未驗證"
        )

    if event.speaker_authorized:
        raise PermissionError(
            "虛構網友不得使用角色本人發言權限"
        )

    if request.comment_id not in event.permitted_comment_ids:
        raise PermissionError(
            "評論不在已審核的事件白名單"
        )


def _check_user(
    request: DispatchRequest,
    user: Mapping,
) -> str:
    if not isinstance(user, Mapping):
        raise TypeError(
            "固定網友資料格式錯誤"
        )

    if str(
        user.get("guild_id", "")
    ) != str(request.guild_id):
        raise PermissionError(
            "虛構網友不屬於目前伺服器"
        )

    if user.get("character_id") != request.character_id:
        raise PermissionError(
            "虛構網友與指定角色沒有有效關係"
        )

    if user.get("id") != request.virtual_user_id:
        raise PermissionError(
            "虛構網友識別碼不一致"
        )

    if user.get("active") not in (True, 1):
        raise PermissionError(
            "虛構網友已停用"
        )

    relation = str(
        user.get("relation_type", "")
    ).strip()

    if relation not in {
        "neutral",
        "fan",
        "superfan",
        "fansite",
        "solo_fan",
        "anti",
        "cp_fan",
    }:
        raise PermissionError(
            "虛構網友關係無效"
        )

    username = user.get("username")

    if (
        not isinstance(username, str)
        or not username.strip()
    ):
        raise ValueError(
            "虛構網友沒有有效的網名"
        )

    return username.strip()


async def prepare_checked_comment(
    request: DispatchRequest,
    services: DispatchServices,
) -> CheckedComment:
    """使用當下的權限及資料建立安全發文內容。

    這裡不會執行 Discord 發文或建立預約。
    """

    validate_request(request)

    if not isinstance(services, DispatchServices):
        raise TypeError(
            "services 必須是 DispatchServices"
        )

    approval = await services.load_approval(
        request
    )

    _check_approval(
        request,
        approval,
    )

    event = await services.load_event(
        request
    )

    _check_event(
        request,
        event,
    )

    user = await services.load_user(
        request
    )

    username = _check_user(
        request,
        user,
    )

    # load_preview 必須使用可信資料來源：
    # 第 40 個檔案會重新核對 SQLite 模板、
    # 發言者類型及當下的固定網友關係。
    template = await services.load_preview(
        event,
        request.comment_id,
    )

    if not isinstance(template, CommentPreview):
        raise TypeError(
            "模板服務必須返回 CommentPreview"
        )

    if (
        template.event_id != request.event_id
        or template.comment_id != request.comment_id
        or template.category_id != event.category_id
    ):
        raise PermissionError(
            "評論模板與本次事件不一致"
        )

    if not template.rendered.strip():
        raise ValueError(
            "評論渲染內容不可為空"
        )

    named = NamedCommentPreview(
        event_id=request.event_id,
        virtual_user_id=request.virtual_user_id,
        username=username,
        relation_type=str(
            user["relation_type"]
        ),
        comment_id=request.comment_id,
        rendered=template.rendered,
        emotion=template.emotion,
        speaker=template.speaker,
    )

    return check_comment_before_publish(
        permission=approval.permission,
        event=event,
        preview=named,
        user_active=True,
        verified_template_id=request.comment_id,
        verified_rendered_text=template.rendered,
    )


async def publish_approved_comment(
    *,
    channel,
    request: DispatchRequest,
    services: DispatchServices,
    publisher=None,
):
    """完成安全檢查後，呼叫正式發文服務。

    若未注入 publisher，使用第 35 個檔案。

    每次正式發布前都重新執行驗證，避免：
    - 審核已撤銷
    - 網友已停用
    - 網友立場已改變
    - 模板已撤銷
    - 事件白名單已更新
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

    if not callable(publisher):
        raise TypeError(
            "publisher 必須是可呼叫函式"
        )

    async def revalidate() -> CheckedComment:
        fresh = await prepare_checked_comment(
            request,
            services,
        )

        if fresh != prepared:
            raise PermissionError(
                "發文前資料已變更，取消發布"
            )

        return fresh

    return await publisher(
        channel=channel,
        comment=prepared,
        revalidate=revalidate,
    )
