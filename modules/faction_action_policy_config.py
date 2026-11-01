"""第21階段：陣營行動規則參數登記（不啟用真實結算）。

僅供已完成 Discord 群主身分驗證的管理服務呼叫；本模組不驗證身分，
不可直接暴露給公開指令。未核定的參數保持缺失狀態。
"""
from __future__ import annotations
import json
import math
from core.database import transaction
from modules.faction_action_settlement_guard import REQUIRED_POLICY_KEYS

_KEYS = frozenset(REQUIRED_POLICY_KEYS)

def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_policy_configs (
            guild_id TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            approved_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')

def _validate(values):
    if not isinstance(values, dict) or not values or set(values) - _KEYS:
        raise ValueError('只能設定正式規則欄位，且至少提供一項。')
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} 必須是有限數值。')
        if value < 0:
            raise ValueError(f'{key} 不可為負數。')
        if 'probability' in key and value > 100:
            raise ValueError(f'{key} 機率不可大於 100。')
        if key == 'effect_duration_seconds' and (value <= 0 or int(value) != value):
            raise ValueError('持續時間必須是正整數秒。')
    return {str(k): int(v) if k == 'effect_duration_seconds' else float(v)
            for k, v in values.items()}

def read_policy(guild_id):
    initialize()
    with transaction() as db:
        row = db.execute('SELECT config_json, revision, approved_by FROM faction_action_policy_configs WHERE guild_id=?', (str(guild_id),)).fetchone()
    values = json.loads(row['config_json']) if row else {}
    return {'values': values, 'revision': row['revision'] if row else 0,
            'missing': [k for k in REQUIRED_POLICY_KEYS if k not in values],
            'approved_by': row['approved_by'] if row else None,
            'execution_enabled': False}

def save_policy(guild_id, updates, *, approved_by, expected_revision):
    """樂觀鎖避免兩位管理員互蓋設定；approved_by 必須由外層驗證。"""
    clean = _validate(updates)
    if not str(approved_by).strip():
        raise ValueError('缺少已驗證的核准者帳號。')
    initialize()
    with transaction() as db:
        row = db.execute('SELECT config_json, revision FROM faction_action_policy_configs WHERE guild_id=?', (str(guild_id),)).fetchone()
        version = row['revision'] if row else 0
        if expected_revision != version:
            raise ValueError('規則版本已變更，請重新載入。')
        values = json.loads(row['config_json']) if row else {}
        values.update(clean)
        db.execute('''INSERT INTO faction_action_policy_configs
          (guild_id,config_json,revision,approved_by) VALUES(?,?,?,?)
          ON CONFLICT(guild_id) DO UPDATE SET config_json=excluded.config_json,
          revision=excluded.revision,approved_by=excluded.approved_by,
          updated_at=CURRENT_TIMESTAMP''',
          (str(guild_id), json.dumps(values, sort_keys=True), version+1, str(approved_by)))
    return read_policy(guild_id)
