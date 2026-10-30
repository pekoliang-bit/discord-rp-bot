"""傳聞事件對固定網友立場的影響：只產生建議，不寫入資料庫。

適用前置系統：core.internet_user_profiles。
不得依未證實傳聞自動轉粉／脫粉，也不變更角色粉絲數。
由管理權限核准後，才可透過既有服務另行提交關係變化。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


EvidenceState = Literal["unverified", "verified", "disproved"]
Phase = Literal["active", "responded", "settled", "cancelled"]
RELATIONS = frozenset({"neutral", "fan", "superfan", "fansite", "solo_fan", "anti", "cp_fan"})


@dataclass(frozen=True, slots=True)
class RumorReactionEvent:
    guild_id: str
    rumor_id: int
    character_id: int
    phase: Phase
    evidence_state: EvidenceState
    public_authorized: bool
    case_verified: bool
    public_response: bool = False


@dataclass(frozen=True, slots=True)
class ReactionProposal:
    virtual_user_id: int
    character_id: int
    old_relation: str
    suggested_relation: str
    old_familiarity: int
    suggested_familiarity: int
    emotion_hint: str
    appearance_multiplier: float
    requires_approval: bool
    reason: str


def propose_reactions(event: RumorReactionEvent, users: list[Mapping]) -> tuple[ReactionProposal, ...]:
    """依網友現有立場提出建議（純計算、無隨機、無副作用）。

    只為同一 character_id 的已篩選網友名單產生提案。
    不能直接用這些提案更新資料庫，必須額外審核且保留稽核紀錄。
    """
    if not isinstance(event.guild_id, str) or not event.guild_id.strip():
        raise ValueError("guild_id 不可為空")
    if type(event.rumor_id) is not int or event.rumor_id <= 0:
        raise ValueError("rumor_id 無效")
    if type(event.character_id) is not int or event.character_id <= 0:
        raise ValueError("character_id 無效")
    if event.phase not in ("active", "responded", "settled", "cancelled"):
        raise ValueError("案件階段無效")
    if event.evidence_state not in ("unverified", "verified", "disproved"):
        raise ValueError("證據狀態無效")

    if not event.case_verified or not event.public_authorized or event.phase == "cancelled":
        return ()

    proposals = []
    seen = set()
    for row in users:
        uid = row.get("id")
        relation = row.get("relation_type")
        familiarity = row.get("familiarity", 0)
        if type(uid) is not int or uid <= 0 or uid in seen:
            raise ValueError("固定網友 ID 無效或重複")
        seen.add(uid)
        if relation not in RELATIONS:
            raise ValueError(f"未知的關係類型：{relation}")
        if type(familiarity) is not int or not 0 <= familiarity <= 100:
            raise ValueError("熟悉度必須是 0～100")
        if "character_id" in row and row["character_id"] != event.character_id:
            raise ValueError("網友資料包含不同藝人角色")
        if "guild_id" in row and str(row["guild_id"]) != event.guild_id:
            raise ValueError("網友資料包含其他伺服器")

        suggested = relation
        target = familiarity
        emotion = "中立"
        weight = 1.0
        reason = "尚無足以改變立場的已確認資訊"

        # 傳聞仍未獲證實：可以討論已公開事件，但絕不提議轉粉或脫粉。
        if event.evidence_state == "unverified":
            if relation == "anti":
                emotion, weight = "質疑／觀望", 1.3
            elif relation in {"fan", "superfan", "fansite", "solo_fan"}:
                emotion, weight = "支持／觀望", 1.25
            else:
                emotion, weight = "觀望", 1.1
            reason = "案件公開但內容未證實，僅調整預覽情緒與出現傾向"
        elif event.evidence_state == "verified":
            if relation == "neutral":
                emotion, weight = "審慎批評", 1.4
                target = max(0, familiarity - 5)
            elif relation == "anti":
                emotion, weight = "批評", 1.5
            elif relation in {"fan", "superfan", "fansite", "solo_fan"}:
                emotion, weight = "失望／維護", 1.3
                target = max(0, familiarity - 8)
            elif relation == "cp_fan":
                emotion, weight = "擔憂", 1.2
            reason = "已核實事件：提供人工審核的熟悉度調整建議"
        else:
            if relation == "anti":
                emotion, weight = "觀望", 0.8
            elif relation in {"fan", "superfan", "fansite", "solo_fan"}:
                emotion, weight = "支持", 1.4
                target = min(100, familiarity + 3)
            else:
                emotion, weight = "中立", 1.0
            reason = "指控已被否定：不得延續指控型評論"

        if event.public_response:
            # 有公開回應不表示其內容真實，僅視為已有可討論的公關事件。
            weight = round(weight * 1.1, 3)

        proposals.append(ReactionProposal(
            virtual_user_id=uid,
            character_id=event.character_id,
            old_relation=relation,
            suggested_relation=suggested,
            old_familiarity=familiarity,
            suggested_familiarity=target,
            emotion_hint=emotion,
            appearance_multiplier=weight,
            requires_approval=(target != familiarity),
            reason=reason,
        ))

    return tuple(proposals)
