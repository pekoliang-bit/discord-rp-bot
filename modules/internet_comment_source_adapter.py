
"""Discord RP 虛構網友評論可信資料來源。

第 40 個檔案完整修正版。

依賴：
- core.database
- core.internet_comment_matcher
- modules.internet_comment_bridge
- modules.internet_comment_dispatch

不會自行發文或修改玩家資料。

正式授權與事件白名單由既有系統提供。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from core.database import get_connection
from core.internet_comment_matcher import (
    TemplateCandidate,
    can_match,
)
from modules.internet_comment_bridge import (
    CommentEvent,
    CommentPreview,
    preview_by_id,
)
from modules.internet_comment_dispatch import (
    DispatchRequest,
    DispatchServices,
)
from modules.internet_comment_review_bridge import (
    CommentPublishApproval,
)


def _positive(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(
            f"{name} 必須為正整數"
        )
    return value


def read_active_virtual_user(
    *,
    guild_id: int,
    character_id: int,
    virtual_user_id: int,
    connection_factory=get_connection,
) -> dict:
    """讀取與指定角色存在有效關係的固定網友。"""

    gid = str(_positive(guild_id, "guild_id"))
    cid = _positive(character_id, "character_id")
    uid = _positive(virtual_user_id, "virtual_user_id")

    db = connection_factory()

    try:
        row = db.execute(
            """
            SELECT
                u.id,
                u.guild_id,
                u.username,
                u.account_type,
                u.influence,
                u.active,
                r.character_id,
                r.relation_type,
                r.familiarity,
                r.appearance_weight

            FROM internet_virtual_users u

            JOIN internet_virtual_user_relations r
              ON r.guild_id = u.guild_id
             AND r.virtual_user_id = u.id

            WHERE u.guild_id = ?
              AND u.id = ?
              AND r.character_id = ?
              AND u.active = 1

            LIMIT 1
            """,
            (gid, uid, cid),
        ).fetchone()

        if row is None:
            raise PermissionError(
                "找不到相同伺服器與角色的有效虛構網友"
            )

        fields = (
            "id",
            "guild_id",
            "username",
            "account_type",
            "influence",
            "active",
            "character_id",
            "relation_type",
            "familiarity",
            "appearance_weight",
        )

        values = {
            name: row[index]
            for index, name in enumerate(fields)
        }

        values["id"] = int(values["id"])
        values["character_id"] = int(
            values["character_id"]
        )
        values["active"] = bool(values["active"])

        return values

    finally:
        db.close()


def read_verified_comment_preview(
    *,
    event: CommentEvent,
    comment_id: str,
    relation_type: str,
    connection_factory=get_connection,
) -> CommentPreview:
    """確認評論與網友立場相符，再重新渲染。"""

    if not isinstance(event, CommentEvent):
        raise TypeError("event 必須是 CommentEvent")

    if not event.verified:
        raise PermissionError("事件尚未核實")

    if event.speaker_authorized:
        raise PermissionError(
            "虛構網友不能使用角色本人授權"
        )

    if comment_id not in event.permitted_comment_ids:
        raise PermissionError(
            "評論不在本次事件白名單"
        )

    db = connection_factory()

    try:
        row = db.execute(
            """
            SELECT
                id,
                raw_text,
                category_id,
                speaker,
                emotion,
                canonical_id,
                requires_authorization

            FROM internet_comment_templates

            WHERE id = ?
              AND category_id = ?
              AND approved = 1
              AND requires_authorization = 0

            LIMIT 1
            """,
            (comment_id, event.category_id),
        ).fetchone()

        if row is None:
            raise PermissionError(
                "找不到可公開使用的已核准模板"
            )

        candidate = TemplateCandidate(
            comment_id=str(row[0]),
            raw_text=str(row[1]),
            category_id=int(row[2]),
            speaker=str(row[3]),
            emotion=str(row[4]),
            canonical_id=str(row[5] or row[0]),
            requires_authorization=bool(row[6]),
        )

        if not can_match(relation_type, candidate):
            raise PermissionError(
                "虛構網友身分與評論發言者不相符"
            )

    finally:
        db.close()

    # 使用既有橋接器的白名單與變數渲染規則。
    preview = preview_by_id(event, comment_id)

    if preview is None:
        raise PermissionError(
            "評論未通過正式渲染條件"
        )

    if (
        preview.event_id != event.event_id
        or preview.comment_id != comment_id
        or preview.category_id != event.category_id
    ):
        raise RuntimeError(
            "評論渲染結果與事件不一致"
        )

    # 兩次讀取之間若模板遭修改，應停止發文。
    if (
        preview.original != candidate.raw_text
        or preview.speaker != candidate.speaker
        or preview.emotion != candidate.emotion
    ):
        raise RuntimeError(
            "評論模板在驗證期間發生變化"
        )

    return preview


def create_dispatch_services(
    request: DispatchRequest,
    *,
    load_approved_review: Callable[
        [DispatchRequest],
        Awaitable[CommentPublishApproval],
    ],
    load_verified_event: Callable[
        [DispatchRequest],
        Awaitable[CommentEvent],
    ],
) -> DispatchServices:
    """建立綁定單次發文請求的可信服務。

    因第 38 個調度器的 load_preview 未包含網友 ID，
    這裡將完整 request 封存在獨立 closure 中。

    每次發文都要重新呼叫本函式建立服務，
    不可把結果當成所有玩家共用的全域物件。
    """

    if not isinstance(request, DispatchRequest):
        raise TypeError(
            "必須提供 DispatchRequest"
        )

    if not callable(load_approved_review):
        raise TypeError(
            "缺少可信審核讀取服務"
        )

    if not callable(load_verified_event):
        raise TypeError(
            "缺少可信事件讀取服務"
        )

    def check_request(actual: DispatchRequest) -> None:
        if actual != request:
            raise PermissionError(
                "不能跨事件、角色或網友共用發文服務"
            )

    async def load_approval(
        actual: DispatchRequest,
    ) -> CommentPublishApproval:
        check_request(actual)
        return await load_approved_review(actual)

    async def load_event(
        actual: DispatchRequest,
    ) -> CommentEvent:
        check_request(actual)
        return await load_verified_event(actual)

    async def load_user(
        actual: DispatchRequest,
    ) -> dict:
        check_request(actual)

        return read_active_virtual_user(
            guild_id=request.guild_id,
            character_id=request.character_id,
            virtual_user_id=request.virtual_user_id,
        )

    async def load_preview(
        event: CommentEvent,
        comment_id: str,
    ) -> CommentPreview:
        if event.event_id != request.event_id:
            raise PermissionError(
                "事件 ID 與綁定請求不同"
            )

        if comment_id != request.comment_id:
            raise PermissionError(
                "評論 ID 與綁定請求不同"
            )

        # 取得當下最新的固定網友關係。
        user = read_active_virtual_user(
            guild_id=request.guild_id,
            character_id=request.character_id,
            virtual_user_id=request.virtual_user_id,
        )

        return read_verified_comment_preview(
            event=event,
            comment_id=comment_id,
            relation_type=str(
                user["relation_type"]
            ),
        )

    return DispatchServices(
        load_approval=load_approval,
        load_event=load_event,
        load_user=load_user,
        load_preview=load_preview,
    )
