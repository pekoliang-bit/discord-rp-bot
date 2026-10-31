
"""傳聞到期結算工作清單（唯讀、安全版）。

安裝：core/rumor_settlement_queue.py
不會初始化資料表、鎖定評級、結案、扣款或發獎。
任何狀態皆只能供後端工作排程或管理面板參考。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.database import get_connection


@dataclass(frozen=True)
class DueCase:
    guild_id: str
    rumor_id: int
    character_id: int
    settle_at: int
    state: str
    explanation: str


def inspect_due_cases(
    guild_id: str | int | None = None,
    *,
    now: int | None = None,
    limit: int = 100,
) -> list[DueCase]:
    """列出到期但仍為 active 的案件，核對結算工作狀態。"""

    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit 必須在 1～500 之間")

    timestamp = (
        int(time.time()) if now is None else int(now)
    )

    gid = (
        None if guild_id is None
        else str(guild_id).strip()
    )

    if gid == "":
        raise ValueError("伺服器編號不可為空")

    with get_connection() as db:
        present = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        if "rp_company_rumors" not in present:
            raise RuntimeError("尚未建立傳聞案件資料表")

        filters = [
            "status='active'",
            "settle_at<=?",
        ]
        values = [timestamp]

        if gid is not None:
            filters.append("guild_id=?")
            values.append(gid)

        values.append(limit)

        rows = db.execute(
            "SELECT guild_id,id,character_id,settle_at "
            "FROM rp_company_rumors WHERE "
            + " AND ".join(filters)
            + " ORDER BY settle_at ASC,id ASC LIMIT ?",
            values,
        ).fetchall()

        output = []

        for row in rows:
            group = str(row["guild_id"])
            rumor_id = int(row["id"])
            cid = int(row["character_id"])

            params = (group, rumor_id, cid)

            if "rp_rumor_grade_snapshots" not in present:
                state = "missing_schema"
                detail = "缺少評級資料表"

            elif not db.execute(
                "SELECT 1 FROM rp_rumor_grade_snapshots "
                "WHERE guild_id=? AND rumor_id=? "
                "AND character_id=?",
                params,
            ).fetchone():
                state = "needs_grade"
                detail = "尚未鎖定正式評級"

            elif "rp_rumor_locked_plans" not in present:
                state = "missing_schema"
                detail = "缺少獎懲方案資料表"

            elif not db.execute(
                "SELECT 1 FROM rp_rumor_locked_plans "
                "WHERE guild_id=? AND rumor_id=? "
                "AND character_id=? AND phase='final'",
                params,
            ).fetchone():
                state = "needs_plan"
                detail = "尚未鎖定正式獎懲方案"

            elif "rp_rumor_reward_jobs" not in present:
                state = "missing_schema"
                detail = "缺少獎懲工作單資料表"

            else:
                job = db.execute(
                    "SELECT state FROM rp_rumor_reward_jobs "
                    "WHERE guild_id=? AND rumor_id=? "
                    "AND character_id=? AND phase='final'",
                    params,
                ).fetchone()

                if job is None:
                    state = "needs_job"
                    detail = "評級與方案齊備；尚無工作單"

                elif job["state"] == "applied":
                    state = "needs_close_check"
                    detail = "工作單完成；須再核對才能結案"

                else:
                    state = "job_pending"
                    detail = "工作單待核對或重試"

            output.append(
                DueCase(
                    guild_id=group,
                    rumor_id=rumor_id,
                    character_id=cid,
                    settle_at=int(row["settle_at"]),
                    state=state,
                    explanation=detail,
                )
            )

        return output
