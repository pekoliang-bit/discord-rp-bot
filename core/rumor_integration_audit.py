
"""傳聞系統整合檢查（唯讀）。

安裝：core/rumor_integration_audit.py

不修改資料、不扣款、不發獎。
不是 Discord Cog，不需要單獨載入。
"""

from __future__ import annotations

from core.database import get_connection


REQUIRED_TABLES = (
    "rp_company_rumors",
    "rp_rumor_case_meta",
    "rp_rumor_category_consent",
    "rp_rumor_actions",
    "rp_rumor_evidence",
    "rp_rumor_investigations",
    "rp_rumor_police_cases",
    "rp_rumor_case_links",
    "rp_rumor_settlement_ledger",
    "rp_rumor_reward_jobs",
)


def audit_rumor_system(
    guild_id: str | int | None = None
) -> dict:
    """唯讀檢查資料庫整合狀況。"""

    report = {
        "tables_present": [],
        "tables_missing": [],
        "checks": {},
        "warnings": [],
    }

    with get_connection() as db:

        existing = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table'"
            ).fetchall()
        }

        report["tables_present"] = [
            t for t in REQUIRED_TABLES
            if t in existing
        ]

        report["tables_missing"] = [
            t for t in REQUIRED_TABLES
            if t not in existing
        ]

        if report["tables_missing"]:
            report["warnings"].append(
                "部分資料表尚未初始化。"
            )

        def count(
            table: str,
            where: str = "",
            params: tuple = (),
        ) -> int | None:

            if table not in existing:
                return None

            query = f"SELECT COUNT(*) FROM {table}"

            if where:
                query += " WHERE " + where

            return int(
                db.execute(query, params).fetchone()[0]
            )

        gid = (
            str(guild_id)
            if guild_id is not None
            else None
        )

        where = "guild_id=?" if gid else ""
        params = (gid,) if gid else ()

        report["checks"]["rumors_total"] = count(
            "rp_company_rumors",
            where,
            params,
        )

        for status in ("active", "settled"):

            condition = (
                (where + " AND " if where else "")
                + "status=?"
            )

            report["checks"][
                f"rumors_{status}"
            ] = count(
                "rp_company_rumors",
                condition,
                params + (status,),
            )

        for state in ("prepared", "applied"):

            condition = (
                (where + " AND " if where else "")
                + "state=?"
            )

            report["checks"][
                f"reward_jobs_{state}"
            ] = count(
                "rp_rumor_reward_jobs",
                condition,
                params + (state,),
            )

        if (
            "rp_company_rumors" in existing
            and "rp_rumor_reward_jobs" in existing
        ):

            query = """
                SELECT COUNT(*)
                FROM rp_company_rumors r
                WHERE r.status='settled'
                AND NOT EXISTS (
                    SELECT 1
                    FROM rp_rumor_reward_jobs j
                    WHERE j.guild_id=r.guild_id
                      AND j.rumor_id=r.id
                      AND j.phase='final'
                )
            """

            if gid:
                query += " AND r.guild_id=?"

            missing = int(
                db.execute(query, params).fetchone()[0]
            )

            report["checks"][
                "settled_without_reward_job"
            ] = missing

            if missing:
                report["warnings"].append(
                    "發現已結案但沒有獎懲工作單的案件。"
                    "可能是舊案或尚未串接的案件，"
                    "不可直接自動補發。"
                )

    return report
