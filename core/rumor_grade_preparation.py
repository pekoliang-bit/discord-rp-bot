"""傳聞正式評級準備入口。安裝：core/rumor_grade_preparation.py。

讀取已核定的門檻、實際公關扣費／效果分數和可信任熱度快照。
預設只進行驗證；lock=True 時儲存 S～D 評級並鎖定獎懲方案。
不發獎、不扣款、不結案；不得直接暴露給 Discord 玩家。

正式部署前，必須在建案的同一筆交易內保存使用中的門檻版本，
並讓熱度引擎、能力／難度修正器接上評級流程。
"""
from __future__ import annotations
from dataclasses import dataclass
from core.database import get_connection
from core.rumor_grade_policy import load_grade_policy
from core.rumor_pr_score_summary import summarize_pr_score
from core.rumor_heat_snapshot import load_heat_snapshot
from core.rumor_grade_service import calculate_grade, lock_grade
from core.rumor_grade_plan_bridge import approve_from_locked_grade
from core.rumor_plan_store import load_case_rules


@dataclass(frozen=True)
class GradePreparation:
    guild_id: str
    rumor_id: int
    character_id: int
    policy_version: str
    score: int
    heat: int
    tier: int
    grade: str
    locked: bool
    plan_fingerprint: str | None


def prepare_rumor_grade(guild_id: str | int, rumor_id: int, *,
                        trusted: bool = False, lock: bool = False) -> GradePreparation:
    """嚴格驗證資料後，選擇性鎖定公關評級與方案。

    評級門檻版本取自案件自己的 rp_rumor_case_grade_policy 表；
    缺少案件版本時禁止臨時套用當前伺服器設定。
    """
    if not trusted:
        raise PermissionError("只有可信任的結算排程可以執行")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件識別資料不合法")
    with get_connection() as db:
        row = db.execute("SELECT character_id,status FROM rp_company_rumors WHERE guild_id=? AND id=?",(gid,rumor_id)).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("案件不存在或已經結案")
        cid = int(row["character_id"])
        policy_row = db.execute("SELECT version FROM rp_rumor_case_grade_policy WHERE guild_id=? AND rumor_id=?",(gid,rumor_id)).fetchone()
        if policy_row is None:
            raise LookupError("案件缺少創案時鎖定的評級門檻版本")
        tier_row = db.execute("SELECT tier FROM rp_rumor_case_tiers WHERE guild_id=? AND rumor_id=?",(gid,rumor_id)).fetchone()
        if tier_row is None:
            raise LookupError("案件沒有正式鎖定的 T 級")
        tier = int(tier_row["tier"])
    rules = load_case_rules(gid,rumor_id)
    policy = load_grade_policy(gid,policy_row["version"])
    summary = summarize_pr_score(gid,rumor_id,cid)
    heat = load_heat_snapshot(gid,rumor_id)
    # 尚未串接能力與事件難度前，純公關行動分數不可用於正式結算。
    score = summary.score
    grade = calculate_grade(score,policy.thresholds)
    if not lock:
        return GradePreparation(gid,rumor_id,cid,policy.version,score,heat.heat,tier,grade,False,None)
    raise RuntimeError(
        "正式評級尚需接入公關能力、案件複雜度與熱度修正；"
        "目前只支援預覽，不可直接鎖定與發獎"
    )
