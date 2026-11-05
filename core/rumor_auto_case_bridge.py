
"""自動刊登→正式傳聞案件；只使用伺服器已核准的規則。"""
from __future__ import annotations

import json
import logging

from core.database import transaction, get_connection
from core.rumor_rule_registry import get_active_rules
from core.rumor_tier_policy import resolve_tier_in_transaction
from core.rumor_complexity_policy import resolve_complexity_in_transaction
from core.rumor_case_creation import insert_case_and_snapshot
from core.rumor_plan_store import initialize as init_snapshots
from modules.pr_rumor import initialize as init_rumors, rumor_allowed, SETTLE_SECONDS

LOG = logging.getLogger(__name__)


def create_linked_case(job_id: str):
    """返回 (rumor_id, reason)。設定不齊時不猜分級，也不扣發獎。"""

    init_rumors()
    init_snapshots()

    with get_connection() as db:
        job = db.execute(
            """
            SELECT guild_id, payload_json, message_id, rumor_id
            FROM rp_rumor_auto_outbox
            WHERE id=?
            """,
            (job_id,),
        ).fetchone()

    if job is None or not job["message_id"]:
        return None, "尚未成功發布訊息"

    if job["rumor_id"]:
        return int(job["rumor_id"]), None

    entry = json.loads(job["payload_json"])
    gid = str(job["guild_id"])
    category = str(entry["category"])

    try:
        rules = get_active_rules(gid)
    except (LookupError, ValueError) as exc:
        return None, f"缺少已啟用的正式獎懲規則：{exc}"

    try:
        with transaction() as db:
            row = db.execute(
                """
                SELECT rumor_id
                FROM rp_rumor_auto_outbox
                WHERE id=?
                """,
                (job_id,),
            ).fetchone()

            if row["rumor_id"]:
                return int(row["rumor_id"]), None

            selected = entry["character_ids"]

            if not selected:
                return None, "沒有授權角色"

            for cid in selected:
                char = db.execute(
                    """
                    SELECT status
                    FROM characters
                    WHERE guild_id=? AND id=?
                    """,
                    (gid, cid),
                ).fetchone()

                if (
                    not char
                    or char["status"] != "active"
                    or not rumor_allowed(db, gid, cid, category)
                ):
                    return None, "角色已停用或撤回授權"

            tier = resolve_tier_in_transaction(
                db,
                gid,
                category,
                "系統",
            )

            complexity = resolve_complexity_in_transaction(
                db,
                gid,
                category,
                "系統",
            )

            rid = insert_case_and_snapshot(
                db,
                guild_id=gid,
                business_id="",
                character_id=int(selected[0]),
                source_type="系統",
                creator_id="system:rumor_autopost",
                title=f"娛樂圈傳聞｜{entry['template_id']}",
                description=entry["text"][:500],
                category=category,
                settle_seconds=SETTLE_SECONDS,
                tier=tier,
                complexity=complexity.complexity,
                complexity_version=complexity.version,
                rules=rules,
                trusted=True,
            )

            db.execute(
                """
                UPDATE rp_rumor_auto_outbox
                SET rumor_id=?
                WHERE id=? AND rumor_id IS NULL
                """,
                (rid, job_id),
            )

            return rid, None

    except (
        LookupError,
        ValueError,
        PermissionError,
        RuntimeError,
    ) as exc:
        return None, f"正式案件設定尚不完整：{exc}"


def reconcile_unlinked(limit=20):
    """原已刊登但缺少規則的傳聞，設定補齊後重新嘗試建案。"""

    with get_connection() as db:
        rows = db.execute(
            """
            SELECT id
            FROM rp_rumor_auto_outbox
            WHERE message_id IS NOT NULL
              AND rumor_id IS NULL
              AND state='done'
            ORDER BY created_at
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    results = []

    for row in rows:
        try:
            results.append(
                (
                    row["id"],
                    *create_linked_case(row["id"]),
                )
            )
        except Exception:
            LOG.exception(
                "無法回補自動傳聞案件 %s",
                row["id"],
            )

    return results
