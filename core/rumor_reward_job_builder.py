
"""傳聞｜從已鎖定方案建立正式獎懲工作單。

安裝：core/rumor_reward_job_builder.py（純工具，非 Discord extension）
依賴：core.rumor_plan_store 與 modules.rumor_settlement。

只建立不可變的 prepared 工作單；不發獎、不結案、不扣款。
建立工作單後，才能由既有結算協調器安全續跑。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from core.database import transaction
from core.rumor_plan_store import load_reward_plan
from modules.rumor_settlement import initialize as initialize_jobs


@dataclass(frozen=True)
class JobBuildResult:
    guild_id: str
    rumor_id: int
    character_id: int
    state: str
    duplicate: bool
    fingerprint: str


def _canonical(changes: dict[str, int]) -> str:
    return json.dumps(changes, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def prepare_locked_reward_job(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    *,
    trusted: bool = False,
) -> JobBuildResult:
    """由已鎖定的 final 方案建立或核對獎懲工作單。

    所有資料由不可變方案載入，不接受外部指定 grade/changes。
    舊案缺少規則或方案時會拒絕，不會自動補發。
    """
    if not trusted:
        raise PermissionError("只有可信任的結算流程可以建立獎懲工作單")
    if type(rumor_id) is not int or rumor_id <= 0:
        raise ValueError("案件編號錯誤")
    if type(character_id) is not int or character_id <= 0:
        raise ValueError("角色編號錯誤")
    gid = str(guild_id).strip()
    if not gid:
        raise ValueError("伺服器編號不可為空")

    # 會核對方案摘要、規則快照與重算結果。
    plan = load_reward_plan(gid, rumor_id, character_id, phase="final")
    if not plan.changes:
        raise ValueError(
            "目前協調器不支援空獎懲工作；"
            "請由結案流程另行記錄無獎懲"
        )

    payload = _canonical(plan.changes)
    initialize_jobs()
    now = int(time.time())

    with transaction() as db:
        rumor = db.execute(
            "SELECT character_id, status FROM rp_company_rumors "
            "WHERE guild_id=? AND id=?",
            (gid, rumor_id),
        ).fetchone()

        if rumor is None or int(rumor["character_id"]) != character_id:
            raise ValueError("案件不存在或角色不符")
        if rumor["status"] not in ("active", "settled"):
            raise ValueError("案件狀態不允許建立結算工作")

        existing = db.execute(
            "SELECT grade, changes_json, state "
            "FROM rp_rumor_reward_jobs "
            "WHERE guild_id=? AND rumor_id=? "
            "AND phase='final' AND character_id=?",
            (gid, rumor_id, character_id),
        ).fetchone()

        # 禁止對無工作單的已結案舊案自動補發。
        if existing is None and rumor["status"] == "settled":
            raise ValueError(
                "舊案已結案但無工作單，"
                "須人工確認，禁止自動回填"
            )

        if existing is None:
            db.execute(
                "INSERT INTO rp_rumor_reward_jobs "
                "(guild_id, rumor_id, phase, character_id, "
                "grade, changes_json, state, created_at, updated_at) "
                "VALUES (?, ?, 'final', ?, ?, ?, 'prepared', ?, ?)",
                (
                    gid,
                    rumor_id,
                    character_id,
                    plan.grade,
                    payload,
                    now,
                    now,
                ),
            )
            return JobBuildResult(
                gid, rumor_id, character_id,
                "prepared", False, plan.fingerprint
            )

        if (
            existing["grade"] != plan.grade
            or existing["changes_json"] != payload
        ):
            raise ValueError(
                "已存在工作單與鎖定方案不一致，拒絕覆蓋"
            )

        return JobBuildResult(
            gid, rumor_id, character_id,
            existing["state"], True, plan.fingerprint
        )
