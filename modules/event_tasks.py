
"""活動多階段任務管理 V1。"""

from __future__ import annotations

import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

from core.database import transaction, get_connection
from modules.event_project_manager import (
    init_db as init_events,
    fetch_event,
    is_event_admin,
    is_owner,
    respond,
    audit,
)


def init_db():
    init_events()

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_event_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                name TEXT NOT NULL,
                requirement TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 1,
                UNIQUE(guild_id, event_id, step)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_event_task_progress (
                guild_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                evidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'submitted',
                submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                reviewed_by TEXT,
                reviewed_at TEXT,
                PRIMARY KEY(guild_id, task_id, user_id)
            )
        """)


def verify_required_tasks(db, gid, eid, uid):
    """獎勵結算前檢查所有必修任務。"""

    missing = db.execute("""
        SELECT t.step, t.name
        FROM rp_event_tasks t
        LEFT JOIN rp_event_task_progress p
          ON p.guild_id=t.guild_id
         AND p.task_id=t.id
         AND p.user_id=?
         AND p.status='approved'
        WHERE t.guild_id=?
          AND t.event_id=?
          AND t.required=1
          AND p.task_id IS NULL
        ORDER BY t.step
    """, (str(uid), str(gid), int(eid))).fetchall()

    if missing:
        labels = "、".join(
            f"第{r['step']}階段：{r['name']}"
            for r in missing[:5]
        )
        raise ValueError(
            f"尚有必修任務未獲官方核准：{labels}"
        )


class EventTasks(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="活動任務建立",
        description="官方設定活動階段與完成條件",
    )
    @app_commands.guild_only()
    async def create_task(
        self,
        i: discord.Interaction,
        活動編號: int,
        階段: int,
        任務名稱: str,
        完成條件: str,
        必修: bool = True,
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "只有群主或正式活動管理員可以設定任務。",
            )

        try:
            if not 1 <= 階段 <= 100:
                raise ValueError("階段必須為1～100。")

            if not 1 <= len(任務名稱.strip()) <= 100:
                raise ValueError("任務名稱需為1～100字。")

            if not 5 <= len(完成條件.strip()) <= 1000:
                raise ValueError("完成條件需為5～1000字。")

            with transaction() as db:
                ev = fetch_event(
                    db, i.guild_id, 活動編號
                )

                if ev["status"] not in ("approved", "open"):
                    raise ValueError(
                        "只能為尚未開始的核准活動設定任務。"
                    )

                if (
                    not is_owner(i)
                    and str(ev["creator_id"]) == str(i.user.id)
                ):
                    raise PermissionError(
                        "管理員不可自行核定自己提交的活動任務。"
                    )

                submitted = db.execute("""
                    SELECT 1
                    FROM rp_event_task_progress
                    WHERE guild_id=? AND event_id=?
                    LIMIT 1
                """, (
                    str(i.guild_id), 活動編號
                )).fetchone()

                if submitted:
                    raise ValueError(
                        "已有玩家提交任務，設定已鎖定。"
                    )

                db.execute("""
                    INSERT INTO rp_event_tasks (
                        guild_id, event_id, step,
                        name, requirement, required
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, event_id, step)
                    DO UPDATE SET
                        name=excluded.name,
                        requirement=excluded.requirement,
                        required=excluded.required
                """, (
                    str(i.guild_id),
                    活動編號,
                    階段,
                    任務名稱.strip(),
                    完成條件.strip(),
                    int(必修),
                ))

                audit(
                    db, i.guild_id, 活動編號,
                    i.user.id, "task_config",
                    f"step={階段}",
                )

            await respond(i, "✅ 階段任務已保存。")

        except (
            ValueError, PermissionError, sqlite3.Error
        ) as e:
            await respond(i, f"⚠️ {e}")

    @app_commands.command(
        name="活動任務列表",
        description="查詢活動階段與自己的任務進度",
    )
    @app_commands.guild_only()
    async def tasks(
        self,
        i: discord.Interaction,
        活動編號: int,
    ):
        try:
            with get_connection() as db:
                ev = fetch_event(
                    db, i.guild_id, 活動編號
                )

                hidden = ev["status"] in (
                    "draft", "submitted", "rejected"
                )

                if hidden and not (
                    is_event_admin(i)
                    or str(ev["creator_id"]) == str(i.user.id)
                ):
                    raise PermissionError(
                        "此企劃尚未公開。"
                    )

                rows = db.execute("""
                    SELECT
                        t.step, t.name,
                        t.requirement, t.required,
                        COALESCE(p.status, '未提交')
                        AS progress
                    FROM rp_event_tasks t
                    LEFT JOIN rp_event_task_progress p
                      ON p.guild_id=t.guild_id
                     AND p.task_id=t.id
                     AND p.user_id=?
                    WHERE t.guild_id=?
                      AND t.event_id=?
                    ORDER BY t.step
                """, (
                    str(i.user.id),
                    str(i.guild_id),
                    活動編號,
                )).fetchall()

            if not rows:
                return await respond(
                    i, "這場活動尚未建立階段任務。"
                )

            lines = []

            for r in rows:
                name = discord.utils.escape_mentions(
                    r["name"][:60]
                )
                condition = discord.utils.escape_mentions(
                    r["requirement"][:120]
                )

                lines.append(
                    f"**第{r['step']}階段｜{name}**"
                    f"｜{'必修' if r['required'] else '選修'}\n"
                    f"條件：{condition}\n"
                    f"進度：{r['progress']}"
                )

            await respond(
                i,
                "🎪 **活動任務進度**\n\n"
                + "\n\n".join(lines),
            )

        except (
            ValueError, PermissionError, sqlite3.Error
        ) as e:
            await respond(i, f"⚠️ {e}")

    @app_commands.command(
        name="活動任務提交",
        description="參加者提交階段任務完成證明",
    )
    @app_commands.guild_only()
    async def submit_task(
        self,
        i: discord.Interaction,
        活動編號: int,
        階段: int,
        完成證明: str,
    ):
        try:
            if not 5 <= len(完成證明.strip()) <= 1500:
                raise ValueError(
                    "完成證明需為5～1500字。"
                )

            with transaction() as db:
                ev = fetch_event(
                    db, i.guild_id, 活動編號
                )

                if ev["status"] != "running":
                    raise ValueError(
                        "活動必須開始後才能提交任務。"
                    )

                enrolled = db.execute("""
                    SELECT 1
                    FROM rp_event_enrollments
                    WHERE guild_id=?
                      AND event_id=?
                      AND user_id=?
                      AND status='accepted'
                """, (
                    str(i.guild_id),
                    活動編號,
                    str(i.user.id),
                )).fetchone()

                if enrolled is None:
                    raise PermissionError(
                        "你尚未通過活動報名。"
                    )

                task = db.execute("""
                    SELECT id FROM rp_event_tasks
                    WHERE guild_id=?
                      AND event_id=?
                      AND step=?
                """, (
                    str(i.guild_id),
                    活動編號,
                    階段,
                )).fetchone()

                if task is None:
                    raise ValueError("找不到指定階段。")

                old = db.execute("""
                    SELECT status
                    FROM rp_event_task_progress
                    WHERE guild_id=?
                      AND task_id=?
                      AND user_id=?
                """, (
                    str(i.guild_id),
                    task["id"],
                    str(i.user.id),
                )).fetchone()

                if old and old["status"] != "rejected":
                    raise ValueError(
                        "此階段已提交或已通過。"
                    )

                db.execute("""
                    INSERT INTO rp_event_task_progress (
                        guild_id, event_id, task_id,
                        user_id, evidence, status
                    )
                    VALUES (?, ?, ?, ?, ?, 'submitted')
                    ON CONFLICT(guild_id, task_id, user_id)
                    DO UPDATE SET
                        evidence=excluded.evidence,
                        status='submitted',
                        submitted_at=CURRENT_TIMESTAMP,
                        reviewed_by=NULL,
                        reviewed_at=NULL
                """, (
                    str(i.guild_id),
                    活動編號,
                    task["id"],
                    str(i.user.id),
                    完成證明.strip(),
                ))

                audit(
                    db, i.guild_id, 活動編號,
                    i.user.id, "task_submit",
                    f"step={階段}",
                )

            await respond(
                i,
                "✅ 完成證明已提交，等待官方審核。",
            )

        except (
            ValueError, PermissionError, sqlite3.Error
        ) as e:
            await respond(i, f"⚠️ {e}")

    @app_commands.command(
        name="活動任務審核",
        description="正式管理員確認玩家階段任務",
    )
    @app_commands.guild_only()
    async def review_task(
        self,
        i: discord.Interaction,
        活動編號: int,
        階段: int,
        玩家: discord.Member,
        通過: bool,
    ):
        if not is_event_admin(i):
            return await respond(
                i,
                "只有群主或正式活動管理員"
                "可以審核任務。",
            )

        try:
            with transaction() as db:
                ev = fetch_event(
                    db, i.guild_id, 活動編號
                )

                if ev["status"] not in ("running", "closed"):
                    raise ValueError(
                        "活動尚未開始或已取消。"
                    )

                if not is_owner(i) and str(i.user.id) in (
                    str(ev["creator_id"]),
                    str(ev["organizer_id"]),
                    str(玩家.id),
                ):
                    raise PermissionError(
                        "不能審核自己主辦或參與的成果。"
                    )

                task = db.execute("""
                    SELECT id FROM rp_event_tasks
                    WHERE guild_id=?
                      AND event_id=?
                      AND step=?
                """, (
                    str(i.guild_id),
                    活動編號,
                    階段,
                )).fetchone()

                if task is None:
                    raise ValueError("找不到指定階段。")

                row = db.execute("""
                    SELECT status
                    FROM rp_event_task_progress
                    WHERE guild_id=?
                      AND task_id=?
                      AND user_id=?
                """, (
                    str(i.guild_id),
                    task["id"],
                    str(玩家.id),
                )).fetchone()

                if row is None or row["status"] != "submitted":
                    raise ValueError(
                        "沒有待審核的完成證明。"
                    )

                db.execute("""
                    UPDATE rp_event_task_progress
                    SET status=?,
                        reviewed_by=?,
                        reviewed_at=CURRENT_TIMESTAMP
                    WHERE guild_id=?
                      AND task_id=?
                      AND user_id=?
                      AND status='submitted'
                """, (
                    "approved" if 通過 else "rejected",
                    str(i.user.id),
                    str(i.guild_id),
                    task["id"],
                    str(玩家.id),
                ))

                audit(
                    db, i.guild_id, 活動編號,
                    i.user.id,
                    "task_approved" if 通過
                    else "task_rejected",
                    f"step={階段},user={玩家.id}",
                )

            await respond(i, "✅ 任務審核完成。")

        except (
            ValueError, PermissionError, sqlite3.Error
        ) as e:
            await respond(i, f"⚠️ {e}")


async def setup(bot: commands.Bot):
    init_db()
    await bot.add_cog(EventTasks(bot))
