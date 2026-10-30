"""網路評論庫與事件系統之間的唯讀橋接層。

本檔不是 Discord Cog，不註冊指令、不自動發布訊息、不更動角色資料。
請由已完成授權及事件審核的上層模組呼叫 preview_comment()。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import AbstractSet

from core import comment_library


@dataclass(frozen=True, slots=True)
class CommentEvent:
    """由上層事件系統建立；布林值必須來自真正的權限／情境驗證。"""
    event_id: str
    category_id: int
    verified: bool = False
    # 此集合必須是已經逐則檢查情境、傳聞、權限等規則的 ID 白名單。
    permitted_comment_ids: frozenset[str] = field(default_factory=frozenset)
    # 可選情緒篩選；原始 JSON 的情緒欄位仍有部分待核對。
    emotion_keywords: tuple[str, ...] = ()
    # 本人／官方發言需另外核准，不能只因為是群主就冒用角色。
    speaker_authorized: bool = False
    primary_name: str | None = None
    secondary_name: str | None = None
    nickname: str | None = None
    fan_name: str | None = None
    animal: str | None = None
    company_name: str | None = None
    staff_name: str | None = None


@dataclass(frozen=True, slots=True)
class CommentPreview:
    event_id: str
    comment_id: str
    category_id: int
    speaker: str
    emotion: str
    original: str
    rendered: str
    trigger_limit: str


# 原評論資料中「角色本人／授權官方帳號」不一定都帶授權標籤。
# 明確補上已審核的特殊 ID，避免被當成普通網友發言。
AUTHOR_ONLY_IDS = frozenset({"C325", "C342"})
# 原稿短句或可能指控未證實事項的評論，必須由事件白名單准入；
# 此橋接層不會從句子本身猜測是否有證據或已實錘。


def _eligible_rows(event: CommentEvent) -> list[dict]:
    if not event.event_id.strip():
        raise ValueError("缺少事件識別碼")
    if event.category_id not in comment_library.CATEGORIES:
        raise ValueError("評論主分類必須為 1～12")
    if not event.verified or not event.permitted_comment_ids:
        return []

    # 目前資料庫上限為 500；每類初始目標是 80 則。
    # 分頁讀取避免未來擴充到 500 則後遺漏評論。
    rows: list[dict] = []
    offset = 0
    while True:
        page = comment_library.list_templates(
            category_id=event.category_id,
            limit=500,
            offset=offset,
            allow_authorized=event.speaker_authorized,
        )
        rows.extend(page)
        if len(page) < 500:
            break
        offset += len(page)

    result = []
    for row in rows:
        if row["id"] not in event.permitted_comment_ids:
            continue
        if row["id"] in AUTHOR_ONLY_IDS and not event.speaker_authorized:
            continue
        if event.emotion_keywords and not any(
            term in str(row["emotion"]) for term in event.emotion_keywords
        ):
            continue
        result.append(row)
    return result


def preview_comment(
    event: CommentEvent, *, rng: random.Random | None = None
) -> CommentPreview | None:
    """從已批准事件白名單中選一則並渲染，無任何 Discord 副作用。

    如果候選資料缺少必要變數，會跳過該候選，不輸出未替換的 ooo。
    不會因這裡生成評論而建立傳聞、粉絲或任何獎勵。
    """
    rows = _eligible_rows(event)
    unique: dict[str, dict] = {}
    for row in rows:
        # C038 和 C074 保留各自 ID，但同類變體不重複加權。
        key = "美神降臨" if row["id"] in {"C038", "C074"} else row["raw_text"]
        unique.setdefault(key, row)
    candidates = list(unique.values())
    (rng or random).shuffle(candidates)

    for row in candidates:
        try:
            rendered = comment_library.render_template(
                row["raw_text"],
                primary_name=event.primary_name,
                secondary_name=event.secondary_name,
                nickname=event.nickname,
                fan_name=event.fan_name,
                animal=event.animal,
                company_name=event.company_name,
                staff_name=event.staff_name,
            )
        except ValueError:
            continue
        return CommentPreview(
            event_id=event.event_id,
            comment_id=row["id"],
            category_id=row["category_id"],
            speaker=row["speaker"],
            emotion=row["emotion"],
            original=row["raw_text"],
            rendered=rendered,
            trigger_limit=row["trigger_limit"],
        )
    return None


def preview_by_id(event: CommentEvent, comment_id: str) -> CommentPreview | None:
    """指定已獲白名單核准的 ID 進行預覽，供管理端測試。"""
    if comment_id not in event.permitted_comment_ids:
        return None
    narrowed = CommentEvent(
        event_id=event.event_id,
        category_id=event.category_id,
        verified=event.verified,
        permitted_comment_ids=frozenset({comment_id}),
        emotion_keywords=event.emotion_keywords,
        speaker_authorized=event.speaker_authorized,
        primary_name=event.primary_name,
        secondary_name=event.secondary_name,
        nickname=event.nickname,
        fan_name=event.fan_name,
        animal=event.animal,
        company_name=event.company_name,
        staff_name=event.staff_name,
    )
    return preview_comment(narrowed)
