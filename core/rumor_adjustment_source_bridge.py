"""從真實公關屬性、複雜度、熱度計算並鎖定評級修正。

安裝：core/rumor_adjustment_source_bridge.py；純工具模組。
本版僅適用已設定完整來源的新案件；正式上線前仍需
在案件建立時鎖定 scoring policy 版本並接通評級排程。
"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import transaction
from core.rumor_scoring_policy import (
    read_active_policy_in_transaction, calculate_adjustments,
)
from core.rumor_pr_ability_source import read_pr_ability_in_transaction
from core.rumor_grade_adjustments import record_adjustment_in_transaction
from core.rumor_heat_snapshot import _digest as heat_digest


@dataclass(frozen=True)
class AdjustmentSourceResult:
    guild_id: str
    rumor_id: int
    character_id: int
    policy_version: str
    attribute_version: int
    complexity_version: str
    heat_event_id: str
    ability_bonus: int
    complexity_penalty: int
    heat_modifier: int


def lock_verified_adjustment(guild_id, rumor_id, *, trusted=False):
    """同一筆交易讀取真實來源並保存快照，不接受玩家提供的分數。"""
    if not trusted:
        raise PermissionError("僅可信任的評級後端可執行")
    gid = str(guild_id).strip()
    if not gid or type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件識別資料不合法")

    with transaction() as db:
        case = db.execute(
            "SELECT character_id,status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?", (gid, rumor_id)
        ).fetchone()
        if case is None or case["status"] != "active":
            raise ValueError("案件不存在或已結案")
        cid = int(case["character_id"])
        ability = read_pr_ability_in_transaction(db, gid, cid)
        complexity = db.execute(
            "SELECT complexity,source_version FROM rp_rumor_complexities "
            "WHERE guild_id=? AND rumor_id=?", (gid, rumor_id)
        ).fetchone()
        if complexity is None:
            raise LookupError("案件缺少建立時鎖定的複雜度")
        heat = db.execute(
            "SELECT heat,source_event_id,digest FROM rp_rumor_heat_snapshots "
            "WHERE guild_id=? AND rumor_id=?", (gid, rumor_id)
        ).fetchone()
        if heat is None:
            raise LookupError("案件尚未鎖定真實熱度快照")
        current_digest = heat_digest(
            gid, rumor_id, int(heat["heat"]), str(heat["source_event_id"])
        )
        if current_digest != heat["digest"]:
            raise RuntimeError("熱度快照完整性不符")
        event = db.execute(
            "SELECT heat FROM rp_rumor_heat_events WHERE guild_id=? "
            "AND rumor_id=? AND source_event_id=?",
            (gid, rumor_id, heat["source_event_id"])
        ).fetchone()
        if event is None or int(event["heat"]) != int(heat["heat"]):
            raise RuntimeError("熱度快照缺少對應的真實來源")
        policy = read_active_policy_in_transaction(db, gid)
        bonus, penalty, modifier = calculate_adjustments(
            policy, ability=ability.value,
            complexity=int(complexity["complexity"]), heat=int(heat["heat"]),
        )
        # 保留所有版本資訊；同一案件若來源變動則拒絕覆寫。
        version = (
            f"{policy.version}:a{ability.attribute_version}:"
            f"c{complexity['source_version']}:h{current_digest[:12]}"
        )
        if len(version) > 80:
            raise ValueError("來源版本太長，請縮短官方版本名稱")
        record_adjustment_in_transaction(
            db, gid, rumor_id, cid,
            ability_bonus=bonus,
            complexity_penalty=penalty,
            heat_modifier=modifier,
            source_version=version, trusted=True,
        )
        return AdjustmentSourceResult(
            gid, rumor_id, cid, policy.version,
            ability.attribute_version, str(complexity["source_version"]),
            str(heat["source_event_id"]), bonus, penalty, modifier,
        )
