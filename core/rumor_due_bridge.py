
"""傳聞結算｜安全續跑與到期檢查（開發版）。

安裝位置：core/rumor_due_bridge.py
此為純工具模組，勿作為 discord.py extension 載入。

安全邊界：
- 只對已存在、內容不可變更的 reward job 執行續跑。
- 已結案但沒有 job 的舊案不自動補發。
- 到期而尚無獎懲方案的案件只列為待處理，不自行結案。
- 不自行決定 S～D 評級、數值或金幣。
- 必須由可信任的定時任務呼叫，不向玩家開放。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from core.database import get_connection
from modules.rumor_settlement import (
    initialize as initialize_jobs,
    settle_character_reward,
)


@dataclass
class BridgeReport:
    due_without_job: list[dict] = field(default_factory=list)
    settled_without_job: list[dict] = field(default_factory=list)
    applied: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "due_without_job": self.due_without_job,
            "settled_without_job": self.settled_without_job,
            "applied": self.applied,
            "failed": self.failed,
            "pending": self.pending,
        }


def audit_due(
    *,
    now: int | None = None,
    guild_id: str | int | None = None,
    limit: int = 100,
) -> BridgeReport:
    """唯讀盤點，不決定評級、不結案、不發獎。"""

    timestamp = (
        int(time.time())
        if now is None
        else int(now)
    )

    limit = max(1, min(int(limit), 500))
    gid = None if guild_id is None else str(guild_id)

    with get_connection() as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        if "rp_company_rumors" not in tables:
            raise RuntimeError(
                "傳聞主資料表尚未初始化。"
            )

        has_jobs = (
            "rp_rumor_reward_jobs" in tables
        )

        conditions = [
            "r.status='active'",
            "r.settle_at<=?",
        ]
        params = [timestamp]

        if gid is not None:
            conditions.append("r.guild_id=?")
            params.append(gid)

        params.append(limit)

        due = [
            dict(row)
            for row in db.execute(
                "SELECT r.guild_id, "
                "r.id AS rumor_id, "
                "r.character_id "
                "FROM rp_company_rumors AS r "
                "WHERE "
                + " AND ".join(conditions)
                + " ORDER BY r.settle_at,r.id "
                "LIMIT ?",
                params,
            )
        ]

        report = BridgeReport()

        for row in due:
            if not has_jobs:
                report.due_without_job.append(row)
                continue

            job = db.execute(
                """
                SELECT state
                FROM rp_rumor_reward_jobs
                WHERE guild_id=?
                  AND rumor_id=?
                  AND phase='final'
                  AND character_id=?
                """,
                (
                    row["guild_id"],
                    row["rumor_id"],
                    row["character_id"],
                ),
            ).fetchone()

            if job:
                report.pending.append(row)
            else:
                report.due_without_job.append(row)

        if has_jobs:
            old_conditions = [
                "r.status='settled'",
                "j.rumor_id IS NULL",
            ]
            old_params = []

            if gid is not None:
                old_conditions.append(
                    "r.guild_id=?"
                )
                old_params.append(gid)

            old_params.append(limit)

            report.settled_without_job = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT r.guild_id,
                           r.id AS rumor_id,
                           r.character_id
                    FROM rp_company_rumors r
                    LEFT JOIN rp_rumor_reward_jobs j
                      ON j.guild_id=r.guild_id
                     AND j.rumor_id=r.id
                     AND j.phase='final'
                     AND j.character_id=r.character_id
                    WHERE
                    """
                    + " AND ".join(old_conditions)
                    + " ORDER BY r.id LIMIT ?",
                    old_params,
                )
            ]

    return report


def resume_prepared(
    *,
    trusted: bool = False,
    guild_id: str | int | None = None,
    limit: int = 100,
) -> BridgeReport:
    """重試已由正式結算器保存的固定工作單。"""

    if not trusted:
        raise PermissionError(
            "只有可信任的系統排程"
            "可執行獎懲續跑。"
        )

    limit = max(1, min(int(limit), 500))
    gid = None if guild_id is None else str(guild_id)

    initialize_jobs()

    with get_connection() as db:
        where = (
            "state='prepared' "
            "AND phase='final'"
        )
        args = []

        if gid is not None:
            where += " AND guild_id=?"
            args.append(gid)

        args.append(limit)

        jobs = [
            dict(row)
            for row in db.execute(
                """
                SELECT guild_id,
                       rumor_id,
                       character_id,
                       grade,
                       changes_json
                FROM rp_rumor_reward_jobs
                WHERE
                """
                + where
                + " ORDER BY created_at,rumor_id "
                  "LIMIT ?",
                args,
            )
        ]

    report = BridgeReport()

    for job in jobs:
        key = {
            name: job[name]
            for name in (
                "guild_id",
                "rumor_id",
                "character_id",
            )
        }

        try:
            changes = json.loads(
                job["changes_json"]
            )

            if not isinstance(changes, dict):
                raise ValueError(
                    "changes_json 格式不正確"
                )

            outcome = settle_character_reward(
                job["guild_id"],
                job["rumor_id"],
                job["character_id"],
                job["grade"],
                changes,
                trusted=True,
            )

            report.applied.append({
                **key,
                "duplicate": outcome.duplicate,
            })

        except Exception as exc:
            report.failed.append({
                **key,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            })

    return report
