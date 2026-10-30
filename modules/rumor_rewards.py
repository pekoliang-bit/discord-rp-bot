
"""傳聞獎懲：只允許已鎖定方案發放角色數值。

位置 modules/rumor_rewards.py；後端工具，非 Discord extension。
不得直接給玩家呼叫 apply_settlement。金幣及特殊粉絲不在此模組處理。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.database import get_connection
from core.stat_rewards import ATTRIBUTES, METRICS, grant_stats

GRADES = frozenset("SABCD")
ALLOWED = (ATTRIBUTES | METRICS) - {"影響指數", "CP好感度"}


@dataclass(frozen=True)
class SettlementPreview:
    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    changes: dict[str, int]
    source_id: str


def _validate_changes(changes: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("數值獎懲必須是非空字典；空方案需由其他結案路徑處理")
    cleaned = {}
    for key, value in changes.items():
        if not isinstance(key, str) or key not in ALLOWED:
            raise ValueError(f"不支援或受管制的獎懲欄位：{key}")
        if type(value) is not int or value == 0 or abs(value) > 1_000_000:
            raise ValueError(f"{key} 的變動必須是安全範圍內的非零整數")
        cleaned[key] = value
    return cleaned


def _normalize_phase(phase: str) -> str:
    if (not isinstance(phase, str) or not phase or len(phase) > 40
            or not all(c.isascii() and (c.isalnum() or c in "_-") for c in phase)):
        raise ValueError("結算階段名稱不合法")
    return phase


def preview_settlement(guild_id, rumor_id, character_id, grade, changes, *, phase="final"):
    gid = str(guild_id).strip()
    if type(rumor_id) is not int or type(character_id) is not int:
        raise ValueError("案件及角色編號必須是整數")
    rid, cid = rumor_id, character_id
    level = str(grade).strip().upper()
    phase = _normalize_phase(phase)
    if not gid or rid <= 0 or cid <= 0 or level not in GRADES:
        raise ValueError("伺服器、案件、角色或評級不合法")
    normalized = _validate_changes(changes)
    with get_connection() as db:
        row = db.execute(
            "SELECT character_id FROM rp_company_rumors WHERE guild_id=? AND id=?",
            (gid, rid),
        ).fetchone()
    if row is None or int(row["character_id"]) != cid:
        raise ValueError("案件不存在，或角色並非主要當事人")
    return SettlementPreview(gid, rid, cid, level, normalized, f"rumor:{rid}:{phase}")


def apply_settlement(preview: SettlementPreview, *, authorized: bool = False):
    """發放前核對永久鎖定的方案；既有結算器的呼叫方式不變。"""
    if not authorized:
        raise PermissionError("只有後端結算器可以發放傳聞獎懲")
    if not isinstance(preview, SettlementPreview):
        raise TypeError("請先使用 preview_settlement")
    prefix = f"rumor:{preview.rumor_id}:"
    if not preview.source_id.startswith(prefix):
        raise ValueError("不合法的來源識別碼")
    phase = _normalize_phase(preview.source_id[len(prefix):])
    verified = preview_settlement(
        preview.guild_id, preview.rumor_id, preview.character_id,
        preview.grade, preview.changes, phase=phase,
    )
    if verified != preview:
        raise ValueError("獎懲預覽資料與重新驗證結果不一致")

    # 防止協調器、資料庫工作單或玩家輸入繞過鎖定方案。
    from core.rumor_plan_store import load_reward_plan
    plan = load_reward_plan(
        verified.guild_id, verified.rumor_id, verified.character_id,
        phase=phase,
    )
    if plan.grade != verified.grade or plan.changes != verified.changes:
        raise ValueError("獎懲內容不符合已鎖定方案，拒絕發放")

    return grant_stats(
        verified.guild_id,
        verified.character_id,
        verified.changes,
        source="rumor",
        source_id=verified.source_id,
        reason=f"傳聞案件 #{verified.rumor_id} {verified.grade} 級獎懲",
        random=False,
        purchased=False,
    )
