"""第21階段：結算預演及不可執行的計畫快照。

只記錄預演結果；沒有任何支付、擲骰、效果發放或通知函式。
不得將此模組的檢查結果視為正式批准或放行。
"""
from __future__ import annotations
import json
from core.database import transaction
from modules.faction_action_policy_config import read_policy
from modules.faction_action_settlement_guard import inspect_settlement


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_settlement_plans (
            guild_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL,
            policy_revision INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status='simulation_only'),
            plan_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,proposal_id)
        )''')


def preview(guild_id, proposal_id):
    policy = read_policy(guild_id)
    report = inspect_settlement(guild_id, proposal_id, approved_policy=policy['values'])
    return {
        'guild_id': str(guild_id), 'proposal_id': int(proposal_id),
        'policy_revision': policy['revision'],
        'action_type': report['action_type'], 'quoted_cost': report['quoted_cost'],
        'checks_passed': bool(report['checks_passed']),
        'issues': list(report['issues']),
        'missing_policy_keys': list(report['missing_policy_keys']),
        'status': 'simulation_only', 'execution_enabled': False,
        'notice': '僅為靜態預演，尚未重新檢查金庫餘額及交易並發安全；禁止扣款或發放效果。',
    }


def store_preview(guild_id, proposal_id):
    """只儲存本次預演快照，不能藉由快照跳過日後重新審核。"""
    report = preview(guild_id, proposal_id)
    initialize()
    with transaction() as db:
        db.execute('''INSERT INTO faction_action_settlement_plans
            (guild_id,proposal_id,policy_revision,status,plan_json)
            VALUES(?,?,?,'simulation_only',?)
            ON CONFLICT(guild_id,proposal_id) DO UPDATE SET
            policy_revision=excluded.policy_revision,status='simulation_only',
            plan_json=excluded.plan_json,updated_at=CURRENT_TIMESTAMP''',
            (str(guild_id),int(proposal_id), report['policy_revision'],
             json.dumps(report,ensure_ascii=False,sort_keys=True)))
    return report


def read_stored_preview(guild_id, proposal_id):
    """僅限有權限的內部服務呼叫；不可公開透出跨陣營情報。"""
    initialize()
    with transaction() as db:
        row = db.execute('''SELECT plan_json FROM faction_action_settlement_plans
            WHERE guild_id=? AND proposal_id=?''',(str(guild_id),int(proposal_id))).fetchone()
    return json.loads(row['plan_json']) if row else None
