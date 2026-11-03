
"""排行榜派發帳本。預設不執行任何資產變更。"""

import json
from core.database import transaction, get_connection


def initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS lb_payout_jobs (
            payout_key TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            reward_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'blocked',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(state IN (
                'blocked', 'ready', 'processing',
                'paid', 'failed'
            ))
        )
    """)


def stage(
    payout_key, guild_id, entity_key,
    source_type, source_id, reward,
):
    """相同來源只能建立一次，且不能修改既有獎勵。"""
    if not payout_key or not entity_key:
        raise ValueError("派發識別碼與獲獎身分不可為空")

    payload = json.dumps(
        reward, ensure_ascii=False, sort_keys=True
    )

    with transaction() as db:
        initialize(db)
        old = db.execute("""
            SELECT * FROM lb_payout_jobs
            WHERE payout_key = ?
        """, (str(payout_key),)).fetchone()

        if old:
            if (
                old["reward_json"] != payload
                or old["entity_key"] != str(entity_key)
                or old["guild_id"] != str(guild_id)
            ):
                raise ValueError("同一派發鍵的資料不一致")
            return False

        db.execute("""
            INSERT INTO lb_payout_jobs (
                payout_key, guild_id, entity_key,
                source_type, source_id, reward_json
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(payout_key), str(guild_id),
            str(entity_key), str(source_type),
            str(source_id), payload,
        ))
    return True


def get_job(payout_key):
    with get_connection() as db:
        initialize(db)
        row = db.execute("""
            SELECT * FROM lb_payout_jobs
            WHERE payout_key = ?
        """, (str(payout_key),)).fetchone()
        return dict(row) if row else None


def list_jobs(guild_id, state=None, limit=100):
    limit = max(1, min(int(limit), 500))
    with get_connection() as db:
        initialize(db)
        sql = """
            SELECT * FROM lb_payout_jobs
            WHERE guild_id = ?
        """
        params = [str(guild_id)]
        if state is not None:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [
            dict(row)
            for row in db.execute(sql, params).fetchall()
        ]
