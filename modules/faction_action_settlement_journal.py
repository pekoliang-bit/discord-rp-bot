"""第24階段：陣營行動結算稽核帳本（不扣款、不發動效果）。

對每次預演保存不可重複的快照，為未來原子結算提供稽核線索。
所有寫入均為 blocked，不可當作已授權的交易。
"""
from __future__ import annotations

import hashlib
import json
from core.database import transaction


def _canonical(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError('結算快照必須是資料物件。')
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def initialize() -> None:
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_settlement_journal (
            guild_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL,
            request_key TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'blocked' CHECK(status='blocked'),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, proposal_id),
            UNIQUE(guild_id, request_key)
        )''')


def record_blocked_preview(guild_id, proposal_id, request_key, snapshot: dict) -> dict:
    gid, pid, key = str(guild_id), int(proposal_id), str(request_key).strip()
    if pid <= 0 or not 1 <= len(key) <= 120:
        raise ValueError('提案或請求編號不合法。')
    document = _canonical(snapshot)
    digest = hashlib.sha256(document.encode('utf-8')).hexdigest()
    initialize()
    with transaction() as db:
        prior_key = db.execute('''SELECT proposal_id FROM faction_action_settlement_journal
            WHERE guild_id=? AND request_key=?''', (gid, key)).fetchone()
        if prior_key and int(prior_key['proposal_id']) != pid:
            raise ValueError('同一請求編號已用於不同提案。')
        prior = db.execute('''SELECT snapshot_hash, request_key FROM faction_action_settlement_journal
            WHERE guild_id=? AND proposal_id=?''', (gid, pid)).fetchone()
        if prior:
            if prior['snapshot_hash'] != digest or prior['request_key'] != key:
                raise ValueError('提案已有不同版本的結算快照，不可覆蓋。')
            return {'created': False, 'status':'blocked', 'snapshot_hash':digest,
                    'execution_enabled':False}
        db.execute('''INSERT INTO faction_action_settlement_journal
            (guild_id,proposal_id,request_key,snapshot_hash,snapshot_json)
            VALUES (?,?,?,?,?)''', (gid, pid, key, digest, document))
        return {'created':True,'status':'blocked','snapshot_hash':digest,
                'execution_enabled':False}


def get_blocked_preview(guild_id, proposal_id) -> dict | None:
    initialize()
    with transaction() as db:
        row = db.execute('''SELECT snapshot_hash,snapshot_json,status,created_at
            FROM faction_action_settlement_journal WHERE guild_id=? AND proposal_id=?''',
            (str(guild_id),int(proposal_id))).fetchone()
        if row is None:
            return None
        return {'snapshot_hash':row['snapshot_hash'], 'snapshot':json.loads(row['snapshot_json']),
                'status':row['status'], 'created_at':row['created_at'], 'execution_enabled':False}
