
"""Discord RP 虛構網友評論的可信資料來源。

接入第 38 個檔案：
    modules.internet_comment_dispatch

提供：
- 依伺服器及角色讀取有效虛構網友
- 重新讀取已核准評論模板
- 檢查網友類型與模板發言者是否相容
- 建立 DispatchServices

不授予管理權限、不建立事件白名單、不發文。
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
        raise ValueError(f"{name} 必須是正整數")
    return value


def read_active_virtual_user(
    *,
    guild_id: int,
    character_id: int,
    virtual_user_id: int,
    connection_factory=get_connection,
) -> dict:
    """唯讀查詢同伺服器、同角色的有效虛構網友。

    不能只查帳號 ID：
    每次都必須核對 guild_id 與 character_id。
    """

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
                "找不到屬於本伺服器及角色的有效網友"
            )

        columns = [
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
        ]

        result = {
            key: row[index]
            for index, key in enumerate(columns)
        }

        result["id"] = int(result["id"])
        result["character_id"] = int(
            result["character_id"]
        )
        result["active"] = bool(result["active"])

        return result

    finally:
        db.close()


def read_verified_comment_preview(
    *,
    event: CommentEvent,
    comment_id: str,
    relation_type: str,
    connection_factory=get_connection,
):
    """從核准的評論模板產生預覽。

    除檢查事件白名單，還必須確認固定網友的
    關係類型可以使用該模板。
    """

    if (
        not event.verified
        or event.speaker_authorized
        or comment_id not in event.permitted_comment_ids
    ):
        raise PermissionError(
            "事件或評論模板未獲虛構網友使用授權"
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
                requires_authorization,
                approved
            FROM internet_comment_templates
            WHERE id = ?
              AND category_id = ?
              AND approved = 1
            LIMIT 1
            """,
            (
                comment_id,
                event.category_id,
            ),
        ).fetchone()

        if row is None:
            raise PermissionError(
                "核准評論模板不存在"
            )

        candidate = TemplateCandidate(
            comment_id=str(row[0]),
            raw_text=str(row[1]),
            category_id=int(row[2]),
            speaker=str(row[3]),
            emotion=str(row[4]),
            canonical_id=str(
                row[5] or row[0]
            ),
            requires_authorization=bool(row[6]),
        )

        if not can_match(relation_type, candidate):
            raise PermissionError(
                "網友身分與評論發言者不相容"
            )

    finally:
        db.close()

    # 延用既有評論橋接函式，重新檢查渲染變數。
    preview = preview_by_id(event, comment_id)

    if preview is None:
        raise PermissionError(
            "評論模板無法通過正式渲染檢查"
        )

    return preview


def create_dispatch_services(
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
    """建立第 38 個調度器需要的資料服務。

    兩個外部回呼必須由正式機器人內部實作：
    - load_approved_review：重新檢查現有 /審核
    - load_verified_event：重新計算事件及評論白名單

    本函式不會把任何事件自動當成已核准。
    """

    if not callable(load_approved_review):
        raise TypeError(
            "缺少正式審核讀取函式"
        )

    if not callable(load_verified_event):
        raise TypeError(
            "缺少已驗證事件讀取函式"
        )

    # 使用各自的非同步函式包裝資料庫操作。
    # 資料庫查詢仍是同步操作；大量留言時，
    # 應由上層另行處理併發與速率限制。

    async def load_user(
        request: DispatchRequest,
    ) -> dict:
        return read_active_virtual_user(
            guild_id=request.guild_id,
            character_id=request.character_id,
            virtual_user_id=request.virtual_user_id,
        )

    async def load_preview(
        event: CommentEvent,
        comment_id: str,
    ):
        raise RuntimeError(
            "須使用帶有固定網友身分的預覽函式"
        )

    # 原始 DispatchServices.load_preview 只有
    # event、comment_id，沒有 virtual_user_id。
    # 這裡不猜測發文者身分，必須由下面的
    # request-bound 服務取得網友關係後才配對。
    #
    # 因此回傳此介面前，由下方 closure 保存
    # 本次經驗證的網友關係；不可跨請求共用。

    raise NotImplementedError(
        "第 38 個 DispatchServices 介面缺少"
        "發文者身分參數，需使用第 41 個檔案"
        "的相容性檢查結果完成介面修正；"
        "禁止跳過身分驗證直接發布。"
    )
