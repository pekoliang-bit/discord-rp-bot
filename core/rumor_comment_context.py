"""傳聞案件 → 評論事件的安全轉換層（不自動發布）。

只接受上層已核實的案件快照。傳聞存在不代表爆料屬實；
不得把「已公開」混同「證據已核實」。
本模組不查詢資料庫、不修改角色數值、不執行 Discord 指令。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Literal

from core.comment_event_rules import VerifiedCommentContext, allowed_comment_ids


RumorPhase = Literal["active", "responded", "settled", "cancelled"]
EvidenceState = Literal["unverified", "verified", "disproved"]


@dataclass(frozen=True, slots=True)
class RumorCommentSnapshot:
    guild_id: str
    rumor_id: int
    character_id: int
    phase: RumorPhase
    visibility: Literal["public", "private"]
    case_record_verified: bool
    disclosure_authorized: bool
    evidence_state: EvidenceState = "unverified"
    has_public_response: bool = False
    response_delay_confirmed: bool = False
    allegation_comment_approved: bool = False
    verified_comment_ids: FrozenSet[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RumorCommentDecision:
    event_id: str
    usable: bool
    category_id: int
    facts: FrozenSet[str]
    permitted_comment_ids: FrozenSet[str]
    reason: str
    evidence_state: str


def _validate(snapshot: RumorCommentSnapshot) -> None:
    if not isinstance(snapshot.guild_id, str) or not snapshot.guild_id.strip():
        raise ValueError("guild_id 不可為空")
    for key in ("rumor_id", "character_id"):
        val = getattr(snapshot, key)
        if type(val) is not int or val <= 0:
            raise ValueError(f"{key} 必須為正整數")
    if snapshot.phase not in {"active", "responded", "settled", "cancelled"}:
        raise ValueError("不支援的案件狀態")
    if snapshot.visibility not in {"public", "private"}:
        raise ValueError("不支援的公開狀態")
    if snapshot.evidence_state not in {"unverified", "verified", "disproved"}:
        raise ValueError("不支援的證據狀態")
    if not isinstance(snapshot.verified_comment_ids, frozenset):
        raise ValueError("verified_comment_ids 必須為 frozenset")
    if any(not isinstance(x, str) for x in snapshot.verified_comment_ids):
        raise ValueError("評論 ID 必須為字串")


def derive_rumor_comment_context(
    snapshot: RumorCommentSnapshot,
    *,
    category_id: int = 7,
    candidate_rows=(),
) -> RumorCommentDecision:
    """轉換已公開案件成保守的事件白名單。

    category_id 7=爭議事件、8=公關回應；兩者須分別評估。
    上層必須從伺服器內的案件資料、公開權限與審核紀錄
    建立 snapshot，而非相信未核實的玩家傳入參數。
    """
    _validate(snapshot)
    if category_id not in (7, 8):
        raise ValueError("傳聞評論只支援分類 7 或 8")
    event_id = f"rumor:{snapshot.guild_id}:{snapshot.rumor_id}:{category_id}"

    if snapshot.phase == "cancelled":
        return RumorCommentDecision(event_id, False, category_id, frozenset(),
                                    frozenset(), "案件已撤銷", snapshot.evidence_state)
    if not snapshot.case_record_verified:
        return RumorCommentDecision(event_id, False, category_id, frozenset(),
                                    frozenset(), "案件紀錄未核實", snapshot.evidence_state)
    if snapshot.visibility != "public" or not snapshot.disclosure_authorized:
        return RumorCommentDecision(event_id, False, category_id, frozenset(),
                                    frozenset(), "未獲公開授權", snapshot.evidence_state)
    if category_id == 8 and not snapshot.has_public_response:
        return RumorCommentDecision(event_id, False, category_id, frozenset(),
                                    frozenset(), "尚無公開公關回應", snapshot.evidence_state)

    facts: set[str] = {"public_controversy"}
    if snapshot.has_public_response:
        facts.add("public_response")
    if snapshot.response_delay_confirmed:
        facts.add("response_delay")
    if snapshot.evidence_state == "verified":
        facts.add("verified_evidence")

    # 排除失實案件的指控評論，即使系統曾經通過個別模板審核。
    if snapshot.evidence_state == "disproved":
        return RumorCommentDecision(event_id, False, category_id, frozenset(facts),
                                    frozenset(), "證據已否定該指控，禁止自動評論", snapshot.evidence_state)

    ctx = VerifiedCommentContext(
        event_id=event_id,
        category_id=category_id,
        event_verified=True,
        facts=frozenset(facts),
        reviewed_comment_ids=snapshot.verified_comment_ids,
        authorized_speaker=False,
        allegation_use_approved=(
            snapshot.evidence_state == "verified"
            and snapshot.allegation_comment_approved
        ),
    )
    allowed = allowed_comment_ids(ctx, candidate_rows)
    return RumorCommentDecision(
        event_id=event_id,
        usable=bool(allowed),
        category_id=category_id,
        facts=ctx.facts,
        permitted_comment_ids=allowed,
        reason="符合事件規則" if allowed else "沒有通過事件條件的評論模板",
        evidence_state=snapshot.evidence_state,
    )
