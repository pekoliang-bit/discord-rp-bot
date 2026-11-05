
"""定時傳聞對接正式案件：每名當事人各自擁有獨立案件和結算。

不改動既有傳聞結算器；同一篇貼文可以連結多筆正式案件。
沒有完整官方規則時僅保留貼文，不產生未核准的獎懲。
"""

from __future__ import annotations

import json
import logging
import time

from core.database import transaction, get_connection
from core.rumor_rule_registry import get_active_rules
from core.rumor_tier_policy import resolve_tier_in_transaction
from core.rumor_complexity_policy import (
    resolve_complexity_in_transaction,
)
from core.rumor_case_creation import insert_case_and_snapshot
from core.rumor_plan_store import initialize as init_snapshots

SETTLE_SECONDS = 72 * 3600

LOG = logging.getLogger(__name__)


def init_rumors():
    from modules.pr_rumor import initialize

    initialize()


def rumor_allowed(db, gid, cid, category):
    from modules.pr_rumor import rumor_allowed as check

    return check(db, gid, cid, category)


def initialize_links():
    """獨立角色案件對照表，重新啟動仍可防止重複建立。"""

    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS
            rp_rumor_autopost_case_links (
                job_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                rumor_id INTEGER NOT NULL,
                PRIMARY KEY(job_id, character_id),
                UNIQUE(guild_id, rumor_id)
            )
        """)


def _published_at(db, job):
    """以 Discord 訊息雪花 ID 還原真正發文時刻。

    舊測試訊息或非 Discord ID 才退回已記錄的刊登時間。
    不使用「補建案件的現在時間」重新計算 72 小時。
    """

    message_id = str(job["message_id"])

    try:
        snowflake = int(message_id)
        published = (
            (snowflake >> 22) + 1420070400000
        ) // 1000

        if (
            1577836800 <= published
            <= int(time.time()) + 60
        ):
            return published

    except (TypeError, ValueError, OverflowError):
        pass

    log = db.execute("""
        SELECT created_at
        FROM rp_rumor_autopost_log
        WHERE guild_id=?
          AND discord_message_id=?
        ORDER BY id
        LIMIT 1
    """, (
        job["guild_id"],
        message_id,
    )).fetchone()

    if log:
        return int(log["created_at"])

    # 舊資料無法從訊息還原時間時，
    # 回退至工作單建立時間。
    return int(job["created_at"])


def linked_cases(job_id):
    """取得單則傳聞對應的所有角色案件。"""

    initialize_links()

    with get_connection() as db:
        return [
            dict(row)
            for row in db.execute("""
                SELECT
                    character_id,
                    rumor_id
                FROM rp_rumor_autopost_case_links
                WHERE job_id=?
                ORDER BY character_id
            """, (str(job_id),))
        ]


def create_linked_case(job_id: str):
    """建立正式案件，返回 (主要案件 ID, 原因)。

    雙人貼文建立兩筆可以分別結算的正式案件。
    多角色建案在同一筆 SQLite 交易內完成。
    已有主要案件時，只補上缺少的角色案件。
    """

    init_rumors()
    init_snapshots()
    initialize_links()

    with get_connection() as db:
        job = db.execute("""
            SELECT
                guild_id,
                payload_json,
                message_id,
                rumor_id,
                created_at
            FROM rp_rumor_auto_outbox
            WHERE id=?
        """, (str(job_id),)).fetchone()

    if job is None or not job["message_id"]:
        return None, "尚未成功發布訊息"

    entry = json.loads(job["payload_json"])

    gid = str(job["guild_id"])
    category = str(entry["category"])

    # 去除重複角色，但維持原先角色順序。
    selected = list(dict.fromkeys(
        int(cid)
        for cid in entry["character_ids"]
    ))

    if not selected:
        return None, "沒有當事角色"

    try:
        rules = get_active_rules(gid)

    except (
        LookupError,
        ValueError,
        RuntimeError,
    ) as exc:
        return None, (
            f"缺少已核准的獎懲規則：{exc}"
        )

    try:
        with transaction() as db:
            current = db.execute("""
                SELECT
                    rumor_id,
                    guild_id,
                    message_id,
                    created_at
                FROM rp_rumor_auto_outbox
                WHERE id=?
            """, (str(job_id),)).fetchone()

            if (
                current is None
                or not current["message_id"]
            ):
                return None, "發布紀錄已失效"

            # 讀取已經建立的角色案件，防止重複建案。
            existing = {
                int(row["character_id"]): int(row["rumor_id"])
                for row in db.execute("""
                    SELECT
                        character_id,
                        rumor_id
                    FROM rp_rumor_autopost_case_links
                    WHERE job_id=?
                """, (str(job_id),))
            }

            primary_id = current["rumor_id"]

            # 相容舊版本：
            # 舊資料可能已經建立主要案件，
            # 但尚未建立角色案件對照紀錄。
            if (
                primary_id
                and selected[0] not in existing
            ):
                primary = db.execute("""
                    SELECT character_id
                    FROM rp_company_rumors
                    WHERE guild_id=?
                      AND id=?
                """, (
                    gid,
                    primary_id,
                )).fetchone()

                if (
                    primary is None
                    or int(primary["character_id"])
                    != selected[0]
                ):
                    raise ValueError(
                        "歷史主要案件與原始角色不一致"
                    )

                db.execute("""
                    INSERT INTO
                    rp_rumor_autopost_case_links (
                        job_id,
                        guild_id,
                        character_id,
                        rumor_id
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    str(job_id),
                    gid,
                    selected[0],
                    primary_id,
                ))

                existing[selected[0]] = int(primary_id)

            if set(existing) - set(selected):
                raise ValueError(
                    "案件當事人對照資料與原貼文不一致"
                )

            # 所有角色都已經建立正式案件。
            if len(existing) == len(selected):
                return existing[selected[0]], None

            # 尚未建案的角色必須保持啟用與授權。
            # 已建好的合法案件不因後續撤回授權而刪除。
            for cid in selected:
                if cid in existing:
                    continue

                character = db.execute("""
                    SELECT status
                    FROM characters
                    WHERE guild_id=?
                      AND id=?
                """, (
                    gid,
                    cid,
                )).fetchone()

                if (
                    not character
                    or character["status"] != "active"
                    or not rumor_allowed(
                        db,
                        gid,
                        cid,
                        category,
                    )
                ):
                    return None, (
                        "角色已停用或撤回傳聞授權"
                    )

            # 不猜測案件等級和複雜度。
            # 必須讀取該伺服器目前正式核准的設定。
            tier = resolve_tier_in_transaction(
                db,
                gid,
                category,
                "系統",
            )

            complexity = (
                resolve_complexity_in_transaction(
                    db,
                    gid,
                    category,
                    "系統",
                )
            )

            published = _published_at(
                db,
                current,
            )

            for cid in selected:
                if cid in existing:
                    continue

                rid = insert_case_and_snapshot(
                    db,
                    guild_id=gid,
                    business_id="",
                    character_id=cid,
                    source_type="系統",
                    creator_id="system:rumor_autopost",
                    title=(
                        "娛樂圈傳聞｜"
                        f"{entry['template_id']}"
                    ),
                    description=entry["text"][:500],
                    category=category,
                    settle_seconds=SETTLE_SECONDS,
                    tier=tier,
                    complexity=complexity.complexity,
                    complexity_version=complexity.version,
                    rules=rules,
                    trusted=True,
                    published_at=published,
                )

                db.execute("""
                    INSERT INTO
                    rp_rumor_autopost_case_links (
                        job_id,
                        guild_id,
                        character_id,
                        rumor_id
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    str(job_id),
                    gid,
                    cid,
                    rid,
                ))

                existing[cid] = rid

            first = existing[selected[0]]

            db.execute("""
                UPDATE rp_rumor_auto_outbox
                SET rumor_id=COALESCE(rumor_id, ?)
                WHERE id=?
            """, (
                first,
                str(job_id),
            ))

            return first, None

    except (
        LookupError,
        ValueError,
        PermissionError,
        RuntimeError,
        TypeError,
    ) as exc:
        return None, (
            f"正式案件設定尚不完整：{exc}"
        )


def reconcile_unlinked(limit=20):
    """補建缺漏案件，不會再次刊登 Discord 訊息。"""

    initialize_links()

    with get_connection() as db:
        # 檢查所有已刊登的工作單。
        # 避免只檢查最舊的500則，
        # 導致新貼文永遠無法補建。
        jobs = db.execute("""
            SELECT
                id,
                payload_json
            FROM rp_rumor_auto_outbox
            WHERE message_id IS NOT NULL
              AND state='done'
            ORDER BY created_at
        """)

        missing = []

        for job in jobs:
            selected = set(
                json.loads(
                    job["payload_json"]
                )["character_ids"]
            )

            linked = {
                int(row[0])
                for row in db.execute("""
                    SELECT character_id
                    FROM rp_rumor_autopost_case_links
                    WHERE job_id=?
                """, (job["id"],))
            }

            if selected - linked:
                missing.append(job["id"])

                if len(missing) >= max(
                    1,
                    min(int(limit), 100),
                ):
                    break

    results = []

    for job_id in missing:
        try:
            results.append((
                job_id,
                *create_linked_case(job_id),
            ))

        except Exception:
            LOG.exception(
                "回補定時傳聞正式案件失敗 %s",
                job_id,
            )

    return results
