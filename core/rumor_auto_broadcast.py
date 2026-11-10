
"""每伺服器獨立的虛構娛樂圈傳聞定時發布服務。"""
from __future__ import annotations

import json
import random
import re
import time
import uuid
from pathlib import Path

from core.database import get_connection, transaction

TEMPLATES_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "rumor_templates"
    / "rumor_templates_100.json"
)

INTERVAL = 8 * 60 * 60

# 僅為此伺服器提供首次建立時的預設設定。
DEFAULT_GUILD_ID = "1544051203493601380"
DEFAULT_CHANNEL_ID = "1545703079691689994"

CHANNEL_RE = re.compile(
    r"^(?:https?://(?:canary\.|ptb\.)?"
    r"discord(?:app)?\.com/channels/\d+/(\d+)"
    r"|<#(\d+)>|(\d+))$"
)


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_extra_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            polarity TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
            character_count INTEGER NOT NULL, created_by TEXT NOT NULL,
            created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)""")
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_optout (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(guild_id, character_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_category_consent (
                guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(guild_id, character_id, category)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_autopost_settings (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                next_due INTEGER,
                updated_by TEXT,
                updated_at INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_autopost_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                template_id TEXT NOT NULL,
                character_ids_json TEXT NOT NULL,
                posted_text TEXT NOT NULL,
                discord_message_id TEXT,
                created_at INTEGER NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_rumor_auto_outbox (
                id TEXT PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                scheduled_at INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                message_id TEXT,
                rumor_id INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_at INTEGER NOT NULL,
                lease_until INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(guild_id, scheduled_at)
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rumor_auto_outbox_due
            ON rp_rumor_auto_outbox(
                state,
                retry_at,
                lease_until
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rumor_auto_history
            ON rp_rumor_autopost_log(guild_id, id)
        """)

        now = int(time.time())

        # 不覆蓋群主已存在的設定。
        db.execute("""
            INSERT OR IGNORE INTO rp_rumor_autopost_settings
            (
                guild_id,
                channel_id,
                enabled,
                next_due,
                updated_by,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            DEFAULT_GUILD_ID,
            DEFAULT_CHANNEL_ID,
            1,
            now + INTERVAL,
            "system_default",
            now,
        ))


def add_template(guild_id, polarity, category, content, creator_id):
    """Add a guild-local approved template without touching the original 100."""
    if polarity not in ('positive', 'negative'):
        raise ValueError('傳聞方向必須是正面或負面')
    category = str(category).strip()
    content = str(content).strip()
    if not (1 <= len(category) <= 40 and 12 <= len(content) <= 900):
        raise ValueError('分類必須1～40字，內文必須12～900字')
    if content.count('{角色}') != 1 or content.count('{角色2}') > 1:
        raise ValueError('內文必須有且只有一個 {角色}，可另加一個 {角色2}')
    if '@everyone' in content or '@here' in content:
        raise ValueError('不可包含全員提及')
    count = 2 if '{角色2}' in content else 1
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_rumor_extra_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            polarity TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
            character_count INTEGER NOT NULL, created_by TEXT NOT NULL,
            created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)""")
        cur = db.execute("""INSERT INTO rp_rumor_extra_templates
          (guild_id,polarity,category,content,character_count,created_by,created_at)
          VALUES (?,?,?,?,?,?,?)""",
          (str(guild_id),polarity,category,content,count,str(creator_id),int(time.time())))
        return f'CUSTOM-{cur.lastrowid:06d}'


def extra_templates(guild_id, db):
    rows = db.execute("""SELECT * FROM rp_rumor_extra_templates
        WHERE guild_id=? AND active=1 ORDER BY id""", (str(guild_id),)).fetchall()
    return [dict(id=f"CUSTOM-{r['id']:06d}", polarity=r['polarity'],
                 category=r['category'], text=r['content'],
                 character_count=r['character_count']) for r in rows]


def templates():
    data = json.loads(
        TEMPLATES_PATH.read_text(encoding="utf-8")
    )

    rows = data["templates"]

    if len(rows) != 100:
        raise ValueError("傳聞模板必須有100則")

    if len({item["id"] for item in rows}) != 100:
        raise ValueError("傳聞模板編號不可重複")

    positive = sum(
        item["polarity"] == "positive"
        for item in rows
    )

    negative = sum(
        item["polarity"] == "negative"
        for item in rows
    )

    if positive != 50 or negative != 50:
        raise ValueError("正負傳聞必須各50則")

    return rows


def parse_channel(value, guild_id):
    raw = str(value).strip()
    match = CHANNEL_RE.fullmatch(raw)

    if not match:
        raise ValueError(
            "請提供Discord頻道連結、提及或ID"
        )

    if "/channels/" in raw:
        guild_part = re.search(
            r"/channels/(\d+)/",
            raw
        )

        if (
            guild_part
            and guild_part.group(1) != str(guild_id)
        ):
            raise ValueError(
                "頻道連結不屬於目前伺服器"
            )

    return str(
        next(
            value
            for value in match.groups()
            if value
        )
    )


def settings(guild_id):
    initialize()

    with get_connection() as db:
        row = db.execute("""
            SELECT *
            FROM rp_rumor_autopost_settings
            WHERE guild_id = ?
        """, (str(guild_id),)).fetchone()

    if row:
        return dict(row)

    return {
        "guild_id": str(guild_id),
        "channel_id": None,
        "enabled": 0,
        "next_due": None,
    }


def set_channel(guild_id, channel_id, actor_id):
    initialize()
    now = int(time.time())

    with transaction() as db:
        db.execute("""
            INSERT INTO rp_rumor_autopost_settings
            (
                guild_id,
                channel_id,
                enabled,
                next_due,
                updated_by,
                updated_at
            )
            VALUES (?, ?, 0, NULL, ?, ?)
            ON CONFLICT(guild_id)
            DO UPDATE SET
                channel_id = excluded.channel_id,
                enabled = 0,
                next_due = NULL,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
        """, (
            str(guild_id),
            str(channel_id),
            str(actor_id),
            now,
        ))


def set_enabled(guild_id, enabled, actor_id):
    initialize()
    now = int(time.time())

    with transaction() as db:
        row = db.execute("""
            SELECT channel_id
            FROM rp_rumor_autopost_settings
            WHERE guild_id = ?
        """, (str(guild_id),)).fetchone()

        if enabled and (
            not row or not row["channel_id"]
        ):
            raise ValueError(
                "請先設定傳聞發布頻道"
            )

        if row:
            db.execute("""
                UPDATE rp_rumor_autopost_settings
                SET enabled = ?,
                    next_due = ?,
                    updated_by = ?,
                    updated_at = ?
                WHERE guild_id = ?
            """, (
                int(bool(enabled)),
                now + INTERVAL if enabled else None,
                str(actor_id),
                now,
                str(guild_id),
            ))
        else:
            db.execute("""
                INSERT INTO rp_rumor_autopost_settings
                (
                    guild_id,
                    channel_id,
                    enabled,
                    next_due,
                    updated_by,
                    updated_at
                )
                VALUES (?, NULL, 0, NULL, ?, ?)
            """, (
                str(guild_id),
                str(actor_id),
                now,
            ))


def due_guild_ids(now=None):
    initialize()

    if now is None:
        now = int(time.time())

    with get_connection() as db:
        rows = db.execute("""
            SELECT guild_id
            FROM rp_rumor_autopost_settings
            WHERE enabled = 1
              AND channel_id IS NOT NULL
              AND next_due <= ?
        """, (int(now),)).fetchall()

    return [
        row["guild_id"]
        for row in rows
    ]


def _eligible(db, guild_id, category):
    # 與原傳聞系統使用同一組授權資料表。
    rows = db.execute("""
        SELECT c.id, c.name
        FROM characters c

        LEFT JOIN rp_rumor_optout o
          ON o.guild_id = c.guild_id
         AND o.character_id = c.id

        LEFT JOIN rp_rumor_category_consent x
          ON x.guild_id = c.guild_id
         AND x.character_id = c.id
         AND x.category = ?

        WHERE c.guild_id = ?
          AND c.status = 'active'
          AND COALESCE(o.enabled, 1) = 1
          AND COALESCE(x.enabled, 1) = 1

        ORDER BY c.id
    """, (
        category,
        str(guild_id),
    )).fetchall()

    return [dict(row) for row in rows]


def choose_entry(db, guild_id, *, rng=None):
    rng = rng or random.SystemRandom()

    recent = {
        row["template_id"]
        for row in db.execute("""
            SELECT template_id
            FROM rp_rumor_autopost_log
            WHERE guild_id = ?
            ORDER BY id DESC
            LIMIT 30
        """, (str(guild_id),))
    }

    possible = []

    for item in templates() + extra_templates(guild_id, db):
        candidates = _eligible(
            db,
            guild_id,
            item["category"]
        )

        if len(candidates) >= item["character_count"]:
            possible.append((
                item,
                candidates
            ))

    if not possible:
        return None

    fresh = [
        entry
        for entry in possible
        if entry[0]["id"] not in recent
    ]

    item, candidates = rng.choice(
        fresh or possible
    )

    selected = rng.sample(
        candidates,
        item["character_count"]
    )

    names = [
        re.sub(
            r"[\r\n\t@`*_<>]",
            "",
            character["name"]
        )[:80]
        for character in selected
    ]

    content = item["text"]

    content = content.replace(
        "{角色2}",
        names[1] if len(names) > 1 else ""
    )

    content = content.replace(
        "{角色}",
        names[0]
    )

    return {
        "template_id": item["id"],
        "polarity": item["polarity"],
        "category": item["category"],
        "character_ids": [
            character["id"]
            for character in selected
        ],
        "text": content,
    }


def claim(guild_id, *, now=None):
    """到期時段先寫入持久化待發佇列。"""

    initialize()
    now = int(time.time()) if now is None else int(now)
    gid = str(guild_id)

    with transaction() as db:
        setting = db.execute(
            """
            SELECT *
            FROM rp_rumor_autopost_settings
            WHERE guild_id=?
            """,
            (gid,)
        ).fetchone()

        if (
            not setting
            or not setting["enabled"]
            or not setting["channel_id"]
            or setting["next_due"] is None
            or setting["next_due"] > now
        ):
            return None

        scheduled = int(setting["next_due"])
        entry = choose_entry(db, gid)

        if entry is None:
            # 沒有可用的授權角色，下一週期再嘗試。
            db.execute(
                """
                UPDATE rp_rumor_autopost_settings
                SET next_due=?
                WHERE guild_id=?
                """,
                (now + INTERVAL, gid)
            )
            return None

        payload = {
            "guild_id": gid,
            "channel_id": str(setting["channel_id"]),
            **entry,
        }

        job_id = uuid.uuid4().hex

        db.execute("""
            INSERT OR IGNORE INTO rp_rumor_auto_outbox
            (
                id,
                guild_id,
                channel_id,
                scheduled_at,
                payload_json,
                retry_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            gid,
            payload["channel_id"],
            scheduled,
            json.dumps(payload, ensure_ascii=False),
            now,
            now,
        ))

        db.execute("""
            UPDATE rp_rumor_autopost_settings
            SET next_due=?
            WHERE guild_id=?
              AND next_due=?
        """, (
            now + INTERVAL,
            gid,
            scheduled,
        ))

        return payload


def pending_jobs(now=None, limit=50):
    initialize()
    now = int(time.time()) if now is None else int(now)

    with get_connection() as db:
        rows = db.execute("""
            SELECT id
            FROM rp_rumor_auto_outbox
            WHERE state IN ('pending','sending','sent')
              AND retry_at<=?
              AND lease_until<=?
            ORDER BY created_at
            LIMIT ?
        """, (
            now,
            now,
            max(1, min(int(limit), 100))
        )).fetchall()

    return [row["id"] for row in rows]


def lease_job(job_id, now=None):
    initialize()
    now = int(time.time()) if now is None else int(now)

    with transaction() as db:
        result = db.execute("""
            UPDATE rp_rumor_auto_outbox
            SET state=CASE
                    WHEN state='pending'
                    THEN 'sending'
                    ELSE state
                END,
                lease_until=?,
                attempts=attempts+1
            WHERE id=?
              AND state IN ('pending','sending','sent')
              AND retry_at<=?
              AND lease_until<=?
        """, (
            now + 180,
            job_id,
            now,
            now,
        ))

        if result.rowcount != 1:
            return None

        row = db.execute("""
            SELECT *
            FROM rp_rumor_auto_outbox
            WHERE id=?
        """, (job_id,)).fetchone()

        return {
            **dict(row),
            "entry": json.loads(row["payload_json"]),
        }


def mark_sent(job_id, message_id):
    with transaction() as db:
        db.execute("""
            UPDATE rp_rumor_auto_outbox
            SET message_id=?,
                state='sent',
                lease_until=0,
                retry_at=0,
                last_error=NULL
            WHERE id=?
              AND state IN ('pending','sending','sent')
        """, (
            str(message_id),
            job_id,
        ))


def mark_retry(job_id, error):
    with transaction() as db:
        row = db.execute("""
            SELECT attempts
            FROM rp_rumor_auto_outbox
            WHERE id=?
        """, (job_id,)).fetchone()

        if row:
            delay = min(
                3600,
                60 * (2 ** min(int(row["attempts"]) - 1, 6))
            )

            db.execute("""
                UPDATE rp_rumor_auto_outbox
                SET lease_until=0,
                    retry_at=?,
                    last_error=?
                WHERE id=?
                  AND state IN ('pending','sending','sent')
            """, (
                int(time.time()) + delay,
                str(error)[:300],
                job_id,
            ))


def finish_job(job_id, rumor_id=None):
    """完成發布登錄；多次呼叫不重複新增紀錄。"""

    with transaction() as db:
        row = db.execute("""
            SELECT *
            FROM rp_rumor_auto_outbox
            WHERE id=?
        """, (job_id,)).fetchone()

        if row is None or not row["message_id"]:
            raise ValueError("尚未有發布訊息")

        entry = json.loads(row["payload_json"])

        if (
            rumor_id is not None
            and row["rumor_id"] not in (None, rumor_id)
        ):
            raise ValueError("已綁定其他案件")

        db.execute("""
            INSERT INTO rp_rumor_autopost_log
            (
                guild_id,
                channel_id,
                template_id,
                character_ids_json,
                posted_text,
                discord_message_id,
                created_at
            )
            SELECT ?,?,?,?,?,?,?
            WHERE NOT EXISTS (
                SELECT 1
                FROM rp_rumor_autopost_log
                WHERE guild_id=?
                  AND discord_message_id=?
            )
        """, (
            entry["guild_id"],
            entry["channel_id"],
            entry["template_id"],
            json.dumps(entry["character_ids"]),
            entry["text"],
            str(row["message_id"]),
            int(time.time()),
            entry["guild_id"],
            str(row["message_id"]),
        ))

        db.execute("""
            UPDATE rp_rumor_auto_outbox
            SET state='done',
                lease_until=0,
                rumor_id=COALESCE(rumor_id, ?),
                last_error=NULL
            WHERE id=?
        """, (rumor_id, job_id))


def skip_job(job_id, reason):
    with transaction() as db:
        db.execute("""
            UPDATE rp_rumor_auto_outbox
            SET state='skipped',
                lease_until=0,
                last_error=?
            WHERE id=?
        """, (
            str(reason)[:300],
            job_id,
        ))


def recent_job_status(gid):
    initialize()

    with get_connection() as db:
        return [
            dict(row)
            for row in db.execute("""
                SELECT
                    id,
                    state,
                    attempts,
                    last_error,
                    rumor_id,
                    created_at
                FROM rp_rumor_auto_outbox
                WHERE guild_id=?
                ORDER BY created_at DESC
                LIMIT 5
            """, (str(gid),))
        ]


def still_consented(guild_id, entry):
    with get_connection() as db:
        selected = entry["character_ids"]

        available = {
            character["id"]
            for character in _eligible(
                db,
                guild_id,
                entry["category"]
            )
        }

        setting = db.execute("""
            SELECT channel_id, enabled
            FROM rp_rumor_autopost_settings
            WHERE guild_id=?
        """, (str(guild_id),)).fetchone()

        return bool(
            setting
            and setting["enabled"]
            and setting["channel_id"] == entry["channel_id"]
            and all(
                character_id in available
                for character_id in selected
            )
        )


def record_post(entry, message_id):
    with transaction() as db:
        db.execute("""
            INSERT INTO rp_rumor_autopost_log
            (
                guild_id,
                channel_id,
                template_id,
                character_ids_json,
                posted_text,
                discord_message_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry["guild_id"],
            entry["channel_id"],
            entry["template_id"],
            json.dumps(entry["character_ids"]),
            entry["text"],
            str(message_id),
            int(time.time()),
        ))


def recent_posts(guild_id, limit=5):
    initialize()
    limit = max(1, min(10, int(limit)))

    with get_connection() as db:
        rows = db.execute("""
            SELECT
                template_id,
                discord_message_id,
                channel_id,
                created_at
            FROM rp_rumor_autopost_log
            WHERE guild_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            str(guild_id),
            limit,
        )).fetchall()

    return [dict(row) for row in rows]
