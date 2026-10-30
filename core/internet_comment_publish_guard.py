
"""Discord RP 虛構網友評論發布安全檢查。

本檔只負責驗證，不會發送 Discord 訊息。

規則：
1. 事件必須已驗證。
2. 評論必須屬於該事件已核准的白名單。
3. 虛構網友不能冒用角色本人或官方帳號。
4. 禁止所有人提及、身分組提及與頻道提及語法。
5. 發布前必須重新驗證，不可只相信舊預覽。
6. 本模組不更改粉絲、金錢、人氣或其他角色數值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from modules.internet_comment_bridge import (
    CommentEvent,
)
from modules.internet_comment_preview import (
    NamedCommentPreview,
)


MAX_COMMENT_LENGTH = 1800
MAX_USERNAME_LENGTH = 32

DISCORD_MENTION_PATTERN = re.compile(
    r"<(?:@!?|@&|#)\d+>"
)

MASS_MENTION_PATTERN = re.compile(
    r"@(?:everyone|here)\b",
    re.IGNORECASE,
)

AUTHOR_ONLY_IDS = frozenset({
    "C325",
    "C342",
})


@dataclass(frozen=True)
class PublishPermission:
    guild_id: int
    channel_id: int
    character_id: int

    # 只有可信的事件管理服務才能給予 True。
    event_authorized: bool = False

    # 只有管理員或事件系統核准後才能給予 True。
    posting_authorized: bool = False

    # 是否允許本次事件進行虛構網友留言。
    simulated_comments_enabled: bool = False


@dataclass(frozen=True)
class CheckedComment:
    guild_id: int
    channel_id: int
    character_id: int

    event_id: str
    virtual_user_id: int
    username: str

    comment_id: str
    rendered: str

    # 發文時使用的固定展示文字。
    content: str


def _positive_int(
    value: int,
    name: str,
) -> None:
    if (
        type(value) is not int
        or value <= 0
    ):
        raise ValueError(
            f"{name} 必須是正整數"
        )


def _validate_display_text(
    value: str,
    name: str,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{name} 必須是字串"
        )

    text = value.strip()

    if not text:
        raise ValueError(
            f"{name} 不可為空"
        )

    if len(text) > maximum:
        raise ValueError(
            f"{name} 超過長度限制 {maximum}"
        )

    if DISCORD_MENTION_PATTERN.search(text):
        raise ValueError(
            f"{name} 含有 Discord 提及語法"
        )

    if MASS_MENTION_PATTERN.search(text):
        raise ValueError(
            f"{name} 含有全體提及"
        )

    if "\x00" in text:
        raise ValueError(
            f"{name} 含有不允許的控制字元"
        )

    return text


def check_publish_permission(
    permission: PublishPermission,
) -> None:
    """檢查管理端已核准的發文權限。"""

    _positive_int(
        permission.guild_id,
        "guild_id",
    )

    _positive_int(
        permission.channel_id,
        "channel_id",
    )

    _positive_int(
        permission.character_id,
        "character_id",
    )

    if not permission.event_authorized:
        raise PermissionError(
            "事件尚未通過核准"
        )

    if not permission.posting_authorized:
        raise PermissionError(
            "沒有發布虛構評論的權限"
        )

    if not permission.simulated_comments_enabled:
        raise PermissionError(
            "本事件未啟用模擬網友留言"
        )


def check_comment_before_publish(
    *,
    permission: PublishPermission,
    event: CommentEvent,
    preview: NamedCommentPreview,
    user_active: bool,
    verified_template_id: str,
    verified_rendered_text: str,
) -> CheckedComment:
    """檢查單則虛構網友留言。

    verified_template_id 與 verified_rendered_text
    必須由正式發布服務重新讀取與渲染取得，
    不能直接取自玩家自行提交的內容。

    注意：
    這個檢查器不會自行查詢資料庫。
    上層必須核實虛構網友確實存在於相同伺服器，
    並且與指定藝人具有有效關係。
    """

    check_publish_permission(permission)

    if not event.verified:
        raise PermissionError(
            "評論事件尚未核實"
        )

    if not event.event_id.strip():
        raise ValueError(
            "事件 ID 不可為空"
        )

    if event.speaker_authorized:
        raise PermissionError(
            "虛構網友不可使用角色本人授權"
        )

    if preview.event_id != event.event_id:
        raise ValueError(
            "留言事件與正式事件不一致"
        )

    _positive_int(
        preview.virtual_user_id,
        "virtual_user_id",
    )

    if not user_active:
        raise PermissionError(
            "虛構網友已停用"
        )

    if (
        preview.comment_id
        not in event.permitted_comment_ids
    ):
        raise PermissionError(
            "評論不在事件核准白名單"
        )

    if preview.comment_id in AUTHOR_ONLY_IDS:
        raise PermissionError(
            "禁止虛構網友冒用角色本人發言"
        )

    if preview.comment_id != verified_template_id:
        raise ValueError(
            "評論 ID 與重新驗證結果不同"
        )

    username = _validate_display_text(
        preview.username,
        "網友名稱",
        MAX_USERNAME_LENGTH,
    )

    rendered = _validate_display_text(
        preview.rendered,
        "留言內容",
        MAX_COMMENT_LENGTH,
    )

    if rendered != verified_rendered_text:
        raise ValueError(
            "留言內容與核准模板不一致"
        )

    # 不使用 Discord Webhook 偽裝真實使用者。
    # 標示為模擬網友評論，避免與真人留言混淆。
    content = (
        f"💬【模擬網友｜{username}】\n"
        f"{rendered}"
    )

    if len(content) > 2000:
        raise ValueError(
            "Discord 訊息超過 2000 字元"
        )

    return CheckedComment(
        guild_id=permission.guild_id,
        channel_id=permission.channel_id,
        character_id=permission.character_id,
        event_id=event.event_id,
        virtual_user_id=preview.virtual_user_id,
        username=username,
        comment_id=preview.comment_id,
        rendered=rendered,
        content=content,
    )
