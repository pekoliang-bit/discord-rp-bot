"""公司員工系統：應徵、工作認證、經驗與各職位升遷方式。

與 rp_approved_company_authorizations 共用正式公司登記。
公司人事與陣營系統完全分離。
"""

from __future__ import annotations

import asyncio
import sqlite3
import re

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

from modules.world_terms import organization_terms

from modules.application_extras import (
    init_db as init_company_authorizations,
)


# ============================================================
# 資料庫初始化
# ============================================================

def initialize():

    init_company_authorizations()

    statements = [

        """
        CREATE TABLE IF NOT EXISTS company_job_positions (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            rank INTEGER NOT NULL CHECK(rank >= 0),
            title TEXT NOT NULL,
            exp_required INTEGER NOT NULL
                CHECK(exp_required >= 0),
            exp_per_work INTEGER NOT NULL
                CHECK(exp_per_work BETWEEN 0 AND 100000),
            promotion_mode TEXT NOT NULL
                CHECK(promotion_mode IN ('auto', 'approval')),
            salary INTEGER NOT NULL DEFAULT 0
                CHECK(salary >= 0),
            PRIMARY KEY(guild_id, company_id, rank)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS company_job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            applicant_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )
        """,

        """
        CREATE UNIQUE INDEX IF NOT EXISTS company_job_pending_unique
        ON company_job_applications(
            guild_id,
            company_id,
            character_id
        )
        WHERE status = 'pending'
        """,

        """
        CREATE TABLE IF NOT EXISTS company_employees (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            rank INTEGER NOT NULL DEFAULT 0,
            exp INTEGER NOT NULL DEFAULT 0 CHECK(exp >= 0),
            status TEXT NOT NULL DEFAULT 'active',
            hired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, company_id, character_id)
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS company_work_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            submitted_by TEXT NOT NULL,
            description TEXT NOT NULL,
            evidence_url TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            exp_awarded INTEGER NOT NULL DEFAULT 0,
            reviewed_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS company_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            target_rank INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        )
        """,

        """
        CREATE UNIQUE INDEX IF NOT EXISTS company_promotion_pending_unique
        ON company_promotions(
            guild_id,
            company_id,
            character_id
        )
        WHERE status = 'pending'
        """,
    ]

    with transaction() as db:
        for statement in statements:
            db.execute(statement)


# ============================================================
# 公司持有人驗證
# ============================================================

def owner(db, gid, company, uid):

    gid = str(gid)
    uid = str(uid)

    row = db.execute(
        """
        SELECT original_applicant_id
        FROM rp_approved_company_authorizations
        WHERE guild_id = ?
          AND company_id = ?
          AND status = 'approved'
        """,
        (gid, company),
    ).fetchone()

    if row is None:

        # 兼容新公司登記資料。
        try:
            newer = db.execute(
                """
                SELECT owner_id
                FROM rp_business_entities
                WHERE guild_id = ?
                  AND business_id = ?
                  AND kind IN ('company','group')
                  AND status = 'active'
                """,
                (gid, company),
            ).fetchone()

        except sqlite3.OperationalError:
            newer = None

        if newer is None:
            raise ValueError(
                "公司尚未經官方正式核准登記。"
            )

        if str(newer["owner_id"]) != uid:
            raise PermissionError(
                "只有已登記的公司負責人可設定職位或審核。"
            )

        return

    if str(row["original_applicant_id"]) != uid:
        raise PermissionError(
            "只有已登記的公司負責人可設定職位或審核。"
        )


def personnel_permission(db, gid, company, uid, permission):
    """舊公司持有人或新公司持有人／被授權管理者的人事權限。"""
    try:
        owner(db, gid, company, uid)
        return
    except PermissionError:
        pass

    from modules.company_business import has_permission

    try:
        permitted = has_permission(db, gid, company, uid, permission)
    except (ValueError, KeyError, sqlite3.OperationalError):
        permitted = False

    if not permitted:
        raise PermissionError(
            "你沒有這間公司的相應人事審核權限。"
        )


# ============================================================
# 角色持有人驗證
# ============================================================

def character_owner(db, gid, cid, uid):

    row = db.execute(
        """
        SELECT owner_id
        FROM characters
        WHERE guild_id = ?
          AND id = ?
          AND status = 'active'
        """,
        (str(gid), int(cid)),
    ).fetchone()

    if (
        row is None
        or str(row["owner_id"]) != str(uid)
    ):
        raise ValueError(
            "角色不存在、未啟用，或不屬於你。"
        )


# ============================================================
# 公司職位設定
# ============================================================

def configure_position(
    gid,
    company,
    uid,
    rank,
    title,
    required,
    per_work,
    mode,
    salary,
):

    if not title.strip() or len(title) > 60:
        raise ValueError(
            "職位名稱需為 1 至 60 字。"
        )

    if rank == 0 and required != 0:
        raise ValueError(
            "入門職位（階級 0）的升職門檻必須是 0。"
        )

    with transaction() as db:

        owner(db, gid, company, uid)

        if rank > 0:

            prior = db.execute(
                """
                SELECT exp_required
                FROM company_job_positions
                WHERE guild_id = ?
                  AND company_id = ?
                  AND rank = ?
                """,
                (
                    gid,
                    company,
                    rank - 1,
                ),
            ).fetchone()

            if (
                prior is None
                or required <= prior["exp_required"]
            ):
                raise ValueError(
                    "請先建立前一級職位，"
                    "且升職門檻必須逐級提高。"
                )

        # Save future position salary edits; do not retroactively price old wages.
        from core.company_salary_snapshots import capture_position_revision
        capture_position_revision(db,gid,company,rank,salary,uid)

        db.execute(
            """
            INSERT INTO company_job_positions (
                guild_id,
                company_id,
                rank,
                title,
                exp_required,
                exp_per_work,
                promotion_mode,
                salary
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, company_id, rank)
            DO UPDATE SET
                title = excluded.title,
                exp_required = excluded.exp_required,
                exp_per_work = excluded.exp_per_work,
                promotion_mode = excluded.promotion_mode,
                salary = excluded.salary
            """,
            (
                gid,
                company,
                rank,
                title.strip(),
                required,
                per_work,
                mode,
                salary,
            ),
        )

    return (
        f"✅ {company} LV.{rank}「{title}」已設定："
        f"每次認證工作 +{per_work} EXP；"
        f"升遷方式 {mode}。"
    )


# ============================================================
# 員工應徵
# ============================================================

def apply_job(gid, company, uid, cid):

    with transaction() as db:

        registered = db.execute(
            """
            SELECT 1
            FROM rp_approved_company_authorizations
            WHERE guild_id = ?
              AND company_id = ?
              AND status = 'approved'
            """,
            (gid, company),
        ).fetchone()

        if not registered:

            try:
                registered = db.execute(
                    """
                    SELECT 1
                    FROM rp_business_entities
                    WHERE guild_id = ?
                      AND business_id = ?
                      AND kind IN ('company','group')
                      AND status = 'active'
                    """,
                    (gid, company),
                ).fetchone()

            except sqlite3.OperationalError:
                registered = None

        if not registered:
            raise ValueError(
                "這家公司尚未正式登記。"
            )

        character_owner(
            db,
            gid,
            cid,
            uid,
        )

        first_position = db.execute(
            """
            SELECT 1
            FROM company_job_positions
            WHERE guild_id = ?
              AND company_id = ?
              AND rank = 0
            """,
            (gid, company),
        ).fetchone()

        if not first_position:
            raise ValueError(
                "公司尚未設定入門職位。"
            )

        employed = db.execute(
            """
            SELECT status
            FROM company_employees
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
            """,
            (gid, company, cid),
        ).fetchone()

        if (
            employed
            and employed["status"] == "active"
        ):
            raise ValueError(
                "這名角色已在這家公司任職。"
            )

        try:

            row = db.execute(
                """
                INSERT INTO company_job_applications (
                    guild_id,
                    company_id,
                    character_id,
                    applicant_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    gid,
                    company,
                    cid,
                    uid,
                ),
            )

        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "這名角色已有待審應徵申請。"
            ) from exc

        return (
            f"✅ 應徵已送出，編號 #{row.lastrowid}，"
            "等待公司負責人審核。"
        )


# ============================================================
# 公司審核應徵
# ============================================================

def review_application(
    gid,
    uid,
    application_id,
    approve,
):

    with transaction() as db:

        row = db.execute(
            """
            SELECT *
            FROM company_job_applications
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                gid,
                application_id,
            ),
        ).fetchone()

        if not row:
            raise ValueError(
                "找不到待審應徵。"
            )

        personnel_permission(
            db,
            gid,
            row["company_id"],
            uid,
            "recruit",
        )

        if str(row["applicant_id"]) == str(uid):
            raise ValueError(
                "不能審核自己的應徵。"
            )

        if approve:

            db.execute(
                """
                INSERT INTO company_employees (
                    guild_id,
                    company_id,
                    character_id,
                    rank,
                    exp,
                    status
                )
                VALUES (?, ?, ?, 0, 0, 'active')
                ON CONFLICT(
                    guild_id,
                    company_id,
                    character_id
                )
                DO UPDATE SET
                    status = 'active'
                """,
                (
                    gid,
                    row["company_id"],
                    row["character_id"],
                ),
            )

        db.execute(
            """
            UPDATE company_job_applications
            SET
                status = ?,
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "approved" if approve else "rejected",
                uid,
                application_id,
            ),
        )

    return (
        "✅ 應徵已錄取。"
        if approve
        else "✅ 應徵已拒絕。"
    )


# ============================================================
# 員工提交工作
# ============================================================

def submit_work(
    gid,
    company,
    uid,
    cid,
    description,
    evidence,
):

    if (
        not description.strip()
        or len(description) > 1000
        or len(evidence) > 500
    ):
        raise ValueError(
            "工作內容需為 1～1000 字，"
            "證明連結最多 500 字。"
        )

    with transaction() as db:

        character_owner(
            db,
            gid,
            cid,
            uid,
        )

        employee = db.execute(
            """
            SELECT 1
            FROM company_employees
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
              AND status = 'active'
            """,
            (
                gid,
                company,
                cid,
            ),
        ).fetchone()

        if not employee:
            raise ValueError(
                "你目前不是這家公司的在職員工。"
            )

        record = db.execute(
            """
            INSERT INTO company_work_records (
                guild_id,
                company_id,
                character_id,
                submitted_by,
                description,
                evidence_url
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                company,
                cid,
                uid,
                description.strip(),
                evidence,
            ),
        )

        return (
            f"✅ 工作紀錄 #{record.lastrowid} 已提交，"
            "等待審核後才會取得 EXP。"
        )


# ============================================================
# 升職檢查
# ============================================================

def advance(db, gid, company, cid):

    employee = db.execute(
        """
        SELECT rank, exp
        FROM company_employees
        WHERE guild_id = ?
          AND company_id = ?
          AND character_id = ?
          AND status = 'active'
        """,
        (
            gid,
            company,
            cid,
        ),
    ).fetchone()

    if not employee:
        return ""

    next_position = db.execute(
        """
        SELECT
            rank,
            exp_required,
            promotion_mode
        FROM company_job_positions
        WHERE guild_id = ?
          AND company_id = ?
          AND rank = ?
        """,
        (
            gid,
            company,
            employee["rank"] + 1,
        ),
    ).fetchone()

    if (
        not next_position
        or employee["exp"] < next_position["exp_required"]
    ):
        return ""

    if next_position["promotion_mode"] == "auto":

        db.execute(
            """
            UPDATE company_employees
            SET rank = ?
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
            """,
            (
                next_position["rank"],
                gid,
                company,
                cid,
            ),
        )

        # 一次最多升一級。
        return (
            f"並已自動升至階級 "
            f"{next_position['rank']}。"
        )

    db.execute(
        """
        INSERT OR IGNORE INTO company_promotions (
            guild_id,
            company_id,
            character_id,
            target_rank
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            gid,
            company,
            cid,
            next_position["rank"],
        ),
    )

    return (
        f"；已達階級 {next_position['rank']} 門檻，"
        "等待老闆批准升職。"
    )


# ============================================================
# 公司審核工作
# ============================================================

def review_work(
    gid,
    uid,
    work_id,
    approve,
):

    with transaction() as db:

        record = db.execute(
            """
            SELECT *
            FROM company_work_records
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                gid,
                work_id,
            ),
        ).fetchone()

        if not record:
            raise ValueError(
                "工作不存在或已審核，不能重複領取經驗。"
            )

        personnel_permission(
            db,
            gid,
            record["company_id"],
            uid,
            "work_review",
        )

        if str(record["submitted_by"]) == str(uid):
            raise ValueError(
                "不能審核自己提交的工作。"
            )

        employee = db.execute(
            """
            SELECT
                e.rank,
                p.exp_per_work
            FROM company_employees e
            JOIN company_job_positions p
                ON p.guild_id = e.guild_id
                AND p.company_id = e.company_id
                AND p.rank = e.rank
            WHERE e.guild_id = ?
              AND e.company_id = ?
              AND e.character_id = ?
              AND e.status = 'active'
            """,
            (
                gid,
                record["company_id"],
                record["character_id"],
            ),
        ).fetchone()

        if not employee:
            raise ValueError(
                "員工狀態或職位設定不存在。"
            )

        exp = (
            employee["exp_per_work"]
            if approve
            else 0
        )

        db.execute(
            """
            UPDATE company_work_records
            SET
                status = ?,
                exp_awarded = ?,
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "approved" if approve else "rejected",
                exp,
                uid,
                work_id,
            ),
        )

        promotion = ""

        if approve:

            db.execute(
                """
                UPDATE company_employees
                SET exp = exp + ?
                WHERE guild_id = ?
                  AND company_id = ?
                  AND character_id = ?
                """,
                (
                    exp,
                    gid,
                    record["company_id"],
                    record["character_id"],
                ),
            )

            promotion = advance(
                db,
                gid,
                record["company_id"],
                record["character_id"],
            )

    if approve:
        return (
            f"✅ 工作已核准，獲得 {exp} EXP"
            f"{promotion}"
        )

    return "✅ 工作已拒絕，未發放 EXP。"


# ============================================================
# 公司審核升職
# ============================================================

def review_promotion(
    gid,
    uid,
    promotion_id,
    approve,
):

    with transaction() as db:

        row = db.execute(
            """
            SELECT *
            FROM company_promotions
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                gid,
                promotion_id,
            ),
        ).fetchone()

        if not row:
            raise ValueError(
                "找不到待審升職。"
            )

        personnel_permission(
            db,
            gid,
            row["company_id"],
            uid,
            "work_review",
        )

        employee = db.execute(
            """
            SELECT rank, exp
            FROM company_employees
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
              AND status = 'active'
            """,
            (
                gid,
                row["company_id"],
                row["character_id"],
            ),
        ).fetchone()

        target = db.execute(
            """
            SELECT exp_required
            FROM company_job_positions
            WHERE guild_id = ?
              AND company_id = ?
              AND rank = ?
            """,
            (
                gid,
                row["company_id"],
                row["target_rank"],
            ),
        ).fetchone()

        if approve:

            if (
                not employee
                or not target
                or employee["rank"] + 1 != row["target_rank"]
                or employee["exp"] < target["exp_required"]
            ):
                raise ValueError(
                    "員工目前已不符合這筆升職的條件。"
                )

            db.execute(
                """
                UPDATE company_employees
                SET rank = ?
                WHERE guild_id = ?
                  AND company_id = ?
                  AND character_id = ?
                """,
                (
                    row["target_rank"],
                    gid,
                    row["company_id"],
                    row["character_id"],
                ),
            )

        db.execute(
            """
            UPDATE company_promotions
            SET
                status = ?,
                reviewed_by = ?,
                reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "approved" if approve else "rejected",
                uid,
                promotion_id,
            ),
        )

    return (
        "✅ 已核准升職。"
        if approve
        else "✅ 已駁回本次升職。"
    )


# ============================================================
# 公司職位總覽
# ============================================================

def overview(gid, company):

    with get_connection() as db:

        jobs = db.execute(
            """
            SELECT *
            FROM company_job_positions
            WHERE guild_id = ?
              AND company_id = ?
            ORDER BY rank
            """,
            (gid, company),
        ).fetchall()

        pending = db.execute(
            """
            SELECT
                (
                    SELECT COUNT(*)
                    FROM company_job_applications
                    WHERE guild_id = ?
                      AND company_id = ?
                      AND status = 'pending'
                ) AS applications,

                (
                    SELECT COUNT(*)
                    FROM company_work_records
                    WHERE guild_id = ?
                      AND company_id = ?
                      AND status = 'pending'
                ) AS works,

                (
                    SELECT COUNT(*)
                    FROM company_promotions
                    WHERE guild_id = ?
                      AND company_id = ?
                      AND status = 'pending'
                ) AS promotions
            """,
            (
                gid,
                company,
                gid,
                company,
                gid,
                company,
            ),
        ).fetchone()

        job_lines = "\n".join(
            (
                f"LV.{job['rank']} "
                f"{job['title']}"
                f"｜門檻 {job['exp_required']} EXP"
                f"｜工作 +{job['exp_per_work']} EXP"
                f"｜升職 "
                f"{'自動' if job['promotion_mode'] == 'auto' else '審核'}"
                f"｜薪資 {job['salary']}"
            )
            for job in jobs
        )

        return (
            "公司職位：\n"
            f"{job_lines or '尚無職位'}"
            "\n\n"
            f"待審：應徵 {pending['applications']}"
            f"／工作 {pending['works']}"
            f"／升職 {pending['promotions']}"
        )


# ============================================================
# 公司待審清單
# ============================================================

def pending_reviews(gid, company, uid):

    with get_connection() as db:

        sections = []

        review_tables = (
            (
                "company_job_applications",
                "應徵",
                "",
            ),
            (
                "company_work_records",
                "工作",
                ",description",
            ),
            (
                "company_promotions",
                "升職",
                ",target_rank",
            ),
        )

        for table, label, extra in review_tables:
            permission = "recruit" if label == "應徵" else "work_review"
            try:
                personnel_permission(db, gid, company, uid, permission)
            except PermissionError:
                continue

            rows = db.execute(
                f"""
                SELECT id, character_id{extra}
                FROM {table}
                WHERE guild_id = ?
                  AND company_id = ?
                  AND status = 'pending'
                ORDER BY id
                LIMIT 15
                """,
                (gid, company),
            ).fetchall()

            items = []

            for row in rows:

                if label == "升職":
                    suffix = (
                        f"｜目標階級 {row['target_rank']}"
                    )

                elif label == "工作":
                    suffix = (
                        f"｜{row['description'][:65]}"
                    )

                else:
                    suffix = ""

                items.append(
                    f"#{row['id']}"
                    f"｜角色 #{row['character_id']}"
                    f"{suffix}"
                )

            sections.append(
                f"**{label}待審**\n"
                + ("\n".join(items) or "無")
            )

        return "\n\n".join(sections)


# ============================================================
# 玩家工作資料
# ============================================================

def personal_status(
    gid,
    company,
    uid,
    cid,
):

    with get_connection() as db:

        character_owner(
            db,
            gid,
            cid,
            uid,
        )

        row = db.execute(
            """
            SELECT
                e.rank,
                e.exp,
                p.title
            FROM company_employees e
            LEFT JOIN company_job_positions p
                ON p.guild_id = e.guild_id
                AND p.company_id = e.company_id
                AND p.rank = e.rank
            WHERE e.guild_id = ?
              AND e.company_id = ?
              AND e.character_id = ?
              AND e.status = 'active'
            """,
            (
                gid,
                company,
                cid,
            ),
        ).fetchone()

        if not row:
            return (
                "目前未在這家公司任職。"
            )

        return (
            f"🏢 {company}"
            f"｜{row['title']}"
            f"（LV.{row['rank']}）"
            f"｜累計 {row['exp']} EXP"
        )


# ============================================================
# 員工辭職
# ============================================================

def resign(
    gid,
    company,
    uid,
    cid,
):
    """
    離職只停用任職狀態。
    不刪除累積經驗或歷史工作紀錄。
    """

    with transaction() as db:
        from core.company_contract_service import guard_resignation, finish_resignation
        guard_resignation(db, gid, company, cid)

        character_owner(
            db,
            gid,
            cid,
            uid,
        )

        updated = db.execute(
            """
            UPDATE company_employees
            SET status = 'resigned'
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
              AND status = 'active'
            """,
            (
                gid,
                company,
                cid,
            ),
        )

        if not updated.rowcount:
            raise ValueError(
                "目前並未在該公司任職。"
            )

        finish_resignation(db, gid, company, cid)

        db.execute(
            """
            UPDATE company_promotions
            SET status = 'cancelled'
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (
                gid,
                company,
                cid,
            ),
        )

        db.execute(
            """
            UPDATE company_work_records
            SET status = 'cancelled'
            WHERE guild_id = ?
              AND company_id = ?
              AND character_id = ?
              AND status = 'pending'
            """,
            (
                gid,
                company,
                cid,
            ),
        )

    return (
        "✅ 已辭職，原有累積 EXP "
        "與歷史紀錄保留。"
    )


# ============================================================
# Discord 公司人事管理指令
# ============================================================

class CompanyWorkforce(commands.Cog):

    company = app_commands.Group(
        name="公司管理",
        description="公司進階人事操作",
        guild_only=True,
    )

    async def run(self, interaction, job):

        await interaction.response.defer(
            ephemeral=True,
        )

        try:
            result = await asyncio.to_thread(job)

        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        message = str(result)
        if interaction.guild_id is not None:
            labels = organization_terms(interaction.guild_id)
            message = re.sub(
                r"公司|集團|品牌|陣營|職位",
                lambda m: {
                    "公司": labels["company"], "集團": labels["group"],
                    "品牌": labels["brand"], "陣營": labels["faction"],
                    "職位": labels["position"],
                }[m.group()],
                message,
            )
        await interaction.followup.send(
            message[:1900],
            ephemeral=True,
        )

    @company.command(
        name="職位設定",
        description="公司負責人設定工作 EXP、薪資與升職方式",
    )
    @app_commands.choices(
        升職方式=[
            app_commands.Choice(
                name="達標自動升職",
                value="auto",
            ),
            app_commands.Choice(
                name="達標後老闆審核",
                value="approval",
            ),
        ]
    )
    async def position(
        self,
        interaction: discord.Interaction,
        公司編號: str,
        階級: app_commands.Range[int, 0, 30],
        職位名稱: str,
        升職門檻: app_commands.Range[int, 0, 100000000],
        每次工作經驗: app_commands.Range[int, 0, 100000],
        升職方式: app_commands.Choice[str],
        薪資: app_commands.Range[int, 0, 100000000] = 0,
    ):

        await self.run(
            interaction,
            lambda: configure_position(
                str(interaction.guild_id),
                公司編號,
                str(interaction.user.id),
                階級,
                職位名稱,
                升職門檻,
                每次工作經驗,
                升職方式.value,
                薪資,
            ),
        )

    @company.command(
        name="職位",
        description="查看公司職位、經驗門檻和待審數",
    )
    async def jobs(
        self,
        interaction: discord.Interaction,
        公司編號: str,
    ):

        await self.run(
            interaction,
            lambda: overview(
                str(interaction.guild_id),
                公司編號,
            ),
        )

    @company.command(
        name="待審",
        description="公司負責人查看應徵、工作與升職審核編號",
    )
    async def pending(
        self,
        interaction: discord.Interaction,
        公司編號: str,
    ):

        await self.run(
            interaction,
            lambda: pending_reviews(
                str(interaction.guild_id),
                公司編號,
                str(interaction.user.id),
            ),
        )

    @company.command(
        name="應徵審核",
        description="公司負責人核准或拒絕應徵",
    )
    async def hiring(
        self,
        interaction: discord.Interaction,
        申請編號: int,
        錄取: bool,
    ):

        await self.run(
            interaction,
            lambda: review_application(
                str(interaction.guild_id),
                str(interaction.user.id),
                申請編號,
                錄取,
            ),
        )

    @company.command(
        name="工作提交",
        description="提交工作與認證紀錄，審核後取得 EXP",
    )
    async def submit(
        self,
        interaction: discord.Interaction,
        公司編號: str,
        角色編號: int,
        工作內容: str,
        證明連結: str = "",
    ):

        await self.run(
            interaction,
            lambda: submit_work(
                str(interaction.guild_id),
                公司編號,
                str(interaction.user.id),
                角色編號,
                工作內容,
                證明連結,
            ),
        )

    @company.command(
        name="工作審核",
        description="公司負責人審核工作並發放職位 EXP",
    )
    async def work_review(
        self,
        interaction: discord.Interaction,
        工作編號: int,
        通過: bool,
    ):

        await self.run(
            interaction,
            lambda: review_work(
                str(interaction.guild_id),
                str(interaction.user.id),
                工作編號,
                通過,
            ),
        )

    @company.command(
        name="升職審核",
        description="核准需人工批准的升職申請",
    )
    async def promotion(
        self,
        interaction: discord.Interaction,
        升職編號: int,
        通過: bool,
    ):

        await self.run(
            interaction,
            lambda: review_promotion(
                str(interaction.guild_id),
                str(interaction.user.id),
                升職編號,
                通過,
            ),
        )

    @company.command(
        name="我的工作",
        description="查看自己角色在指定公司的職位與 EXP",
    )
    async def my_job(
        self,
        interaction: discord.Interaction,
        公司編號: str,
        角色編號: int,
    ):

        await self.run(
            interaction,
            lambda: personal_status(
                str(interaction.guild_id),
                公司編號,
                str(interaction.user.id),
                角色編號,
            ),
        )


async def setup(bot):

    await asyncio.to_thread(initialize)

    await bot.add_cog(
        CompanyWorkforce()
    )
