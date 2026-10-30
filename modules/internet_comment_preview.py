"""固定網友與評論模板的多帳號留言預覽整合層。

資料來源：已授權的事件 CommentEvent、同伺服器的虛構網友清單。
功能：出場選擇、留言角色配對、渲染預覽；不建立預約、不寫資料庫、不發文。
正式發文前仍須重新驗證事件、權限及 idempotency。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from core.internet_user_selector import SelectionPolicy, select_commenters
from core.internet_comment_matcher import can_match, TemplateCandidate
from modules.internet_comment_bridge import CommentEvent, CommentPreview, preview_by_id
from core import comment_library


@dataclass(frozen=True, slots=True)
class NamedCommentPreview:
    event_id: str
    virtual_user_id: int
    username: str
    relation_type: str
    comment_id: str
    rendered: str
    emotion: str
    speaker: str


@dataclass(frozen=True, slots=True)
class BatchPreview:
    event_id: str
    comments: tuple[NamedCommentPreview, ...]
    selected_users: int
    skipped_users: int


def _load_eligible(event: CommentEvent) -> list[dict]:
    """使用既有 SQLite 評論庫，僅查詢事件白名單所需分類。"""
    rows: list[dict] = []
    offset = 0
    while True:
        page = comment_library.list_templates(
            category_id=event.category_id,
            limit=500,
            offset=offset,
            allow_authorized=False,
        )
        rows.extend(row for row in page if row["id"] in event.permitted_comment_ids)
        if len(page) < 500:
            break
        offset += len(page)
    return rows


def build_named_previews(
    *,
    event: CommentEvent,
    event_type: str,
    virtual_user_rows: Iterable[Mapping],
    recent_posted_ids: Iterable[int] = (),
    policy: SelectionPolicy | None = None,
    rng: random.Random | None = None,
    template_rows: Iterable[Mapping] | None = None,
    preview_function: Callable[[CommentEvent, str], CommentPreview | None] = preview_by_id,
) -> BatchPreview:
    """產生一次完整留言預覽，絕不發布。

    virtual_user_rows 必須事先由可信服務依 guild_id、character_id 篩選。
    template_rows 僅供測試注入；正式使用時由既有 SQLite 模組載入。
    preview_function 可供離線單元測試注入。
    """
    if not event.event_id.strip():
        raise ValueError("event_id 不可為空")
    if not event.verified or not event.permitted_comment_ids:
        return BatchPreview(event.event_id, (), 0, 0)
    if event.speaker_authorized:
        # 此模組代表虛構網友，不得利用角色本人授權借用官方身分。
        raise ValueError("虛構網友預覽不能使用角色本人授權")

    generator = rng or random.SystemRandom()
    selection = select_commenters(
        event.event_id,
        event_type,
        virtual_user_rows,
        policy=policy,
        recent_event_ids=recent_posted_ids,
        rng=generator,
    )
    templates = list(template_rows) if template_rows is not None else _load_eligible(event)
    used_texts: set[str] = set()
    used_comment_ids: set[str] = set()
    comments: list[NamedCommentPreview] = []

    for user in selection.selected:
        possible: list[str] = []
        for row in templates:
            cid = str(row.get("id", ""))
            if cid not in event.permitted_comment_ids or row.get("category_id") != event.category_id:
                continue
            if row.get("approved", 1) not in (1, True):
                continue
            candidate = TemplateCandidate(
                comment_id=cid,
                raw_text=str(row.get("raw_text", row.get("text", ""))),
                category_id=event.category_id,
                speaker=str(row.get("speaker", "")),
                emotion=str(row.get("emotion", "")),
                canonical_id=str(row.get("canonical_id") or cid),
                requires_authorization=bool(row.get("requires_authorization", False)),
            )
            if not can_match(user.relation_type, candidate):
                continue
            if event.emotion_keywords and not any(
                word in candidate.emotion for word in event.emotion_keywords
            ):
                continue
            if cid not in used_comment_ids:
                possible.append(cid)
        generator.shuffle(possible)
        for cid in possible:
            # 正式渲染、授權、未填變數檢查沿用同一既有橋接函式。
            preview = preview_function(event, cid)
            if preview is None or preview.comment_id != cid:
                continue
            if preview.rendered in used_texts:
                continue
            used_texts.add(preview.rendered)
            used_comment_ids.add(cid)
            comments.append(NamedCommentPreview(
                event_id=event.event_id,
                virtual_user_id=user.id,
                username=user.username,
                relation_type=user.relation_type,
                comment_id=cid,
                rendered=preview.rendered,
                emotion=preview.emotion,
                speaker=preview.speaker,
            ))
            break

    return BatchPreview(
        event_id=event.event_id,
        comments=tuple(comments),
        selected_users=len(selection.selected),
        skipped_users=len(selection.selected) - len(comments),
    )
