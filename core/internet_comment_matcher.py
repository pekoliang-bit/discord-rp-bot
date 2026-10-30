"""將虛構網友身分與已通過事件審核的評論模板配對（純計算）。

配對不是發布授權。本模組不操作資料庫、不建立帳號、不發送 Discord 訊息。
候選評論必須先由上層事件規則確認為 approved 且屬於 allowed_ids。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Mapping


RELATIONS = frozenset({"neutral", "fan", "superfan", "fansite", "solo_fan", "anti", "cp_fan"})

# 依原始 419 則資料的 speaker 標籤建立明確的對應關係。
# 「角色本人／授權官方帳號」不屬於任何虛構網友，永遠不配對。
ROLE_TOKENS = {
    "neutral": ("路人", "觀眾", "參與者", "消費者", "關注者", "吃瓜", "觀察者", "質疑者", "批評者", "支持者"),
    "fan": ("粉絲", "觀眾", "顏粉", "劇粉", "支持者", "參與者"),
    "superfan": ("粉絲", "觀眾", "顏粉", "劇粉", "支持者", "參與者"),
    "fansite": ("粉絲", "觀眾", "顏粉", "參與者"),
    "solo_fan": ("唯粉", "粉絲", "顏粉", "觀眾", "參與者"),
    "anti": ("黑粉", "質疑者", "批評者", "反諷者"),
    "cp_fan": ("CP粉", "粉絲", "觀眾", "參與者"),
}

AUTHOR_MARKERS = ("角色本人", "官方帳號", "工作人員本人")
HIGH_RISK_MARKERS = ("爆料者",)


@dataclass(frozen=True, slots=True)
class TemplateCandidate:
    comment_id: str
    raw_text: str
    category_id: int
    speaker: str
    emotion: str
    canonical_id: str
    requires_authorization: bool = False


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    virtual_user_id: int
    relation_type: str
    comment_id: str
    canonical_id: str
    raw_text: str
    speaker: str
    emotion: str


def _candidate(row: Mapping) -> TemplateCandidate:
    comment_id = str(row.get("id", "")).strip()
    if not comment_id.startswith("C") or not comment_id[1:].isdigit():
        raise ValueError(f"評論 ID 無效：{comment_id!r}")
    raw_text = row.get("raw_text", row.get("text"))
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError(f"{comment_id} 缺少評論原文")
    category_id = row.get("category_id")
    if type(category_id) is not int or not 1 <= category_id <= 12:
        raise ValueError(f"{comment_id} 主分類無效")
    return TemplateCandidate(
        comment_id=comment_id,
        raw_text=raw_text,
        category_id=category_id,
        speaker=str(row.get("speaker", "")),
        emotion=str(row.get("emotion", "")),
        canonical_id=str(row.get("canonical_id") or comment_id),
        requires_authorization=bool(row.get("requires_authorization", False)),
    )


def can_match(relation_type: str, candidate: TemplateCandidate) -> bool:
    """保守配對：無法識別 speaker、需授權或角色本人模板一律拒絕。"""
    if relation_type not in RELATIONS:
        return False
    if candidate.requires_authorization:
        return False
    if candidate.comment_id in {"C325", "C342"}:
        return False
    if any(marker in candidate.speaker for marker in AUTHOR_MARKERS + HIGH_RISK_MARKERS):
        return False
    return any(token in candidate.speaker for token in ROLE_TOKENS[relation_type])


def match_one(
    *,
    virtual_user_id: int,
    relation_type: str,
    candidates: Iterable[Mapping],
    category_id: int,
    allowed_ids: frozenset[str],
    event_verified: bool,
    used_canonical_ids: frozenset[str] = frozenset(),
    rng: random.Random | None = None,
) -> TemplateMatch | None:
    """從事件白名單抽取一則符合網友立場的評論。

    allowed_ids 必須由可信的事件規則服務供應，不能直接採用玩家輸入。
    同內容（含 C038/C074）不增加抽取權重。
    """
    if type(virtual_user_id) is not int or virtual_user_id <= 0:
        raise ValueError("virtual_user_id 必須是正整數")
    if relation_type not in RELATIONS:
        raise ValueError("未知的網友關係類型")
    if type(category_id) is not int or not 1 <= category_id <= 12:
        raise ValueError("category_id 必須為 1～12")
    if not event_verified or not allowed_ids:
        return None

    unique: dict[str, TemplateCandidate] = {}
    for row in candidates:
        if row.get("approved", 1) not in (1, True):
            continue
        candidate = _candidate(row)
        if candidate.comment_id not in allowed_ids or candidate.category_id != category_id:
            continue
        if not can_match(relation_type, candidate):
            continue
        canonical = "美神降臨" if candidate.comment_id in {"C038", "C074"} else candidate.raw_text
        if candidate.canonical_id in used_canonical_ids or canonical in used_canonical_ids:
            continue
        unique.setdefault(canonical, candidate)

    if not unique:
        return None
    picked = (rng or random.SystemRandom()).choice(list(unique.values()))
    return TemplateMatch(
        virtual_user_id=virtual_user_id,
        relation_type=relation_type,
        comment_id=picked.comment_id,
        canonical_id=picked.canonical_id,
        raw_text=picked.raw_text,
        speaker=picked.speaker,
        emotion=picked.emotion,
    )
