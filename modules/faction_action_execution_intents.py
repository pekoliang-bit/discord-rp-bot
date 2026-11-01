"""陣營行動第22階段：結算意圖與冪等紀錄（仍禁止正式執行）。

僅供已驗證授權的內部服務呼叫。建立意圖不代表預留經費或執行成功。
正式執行器必須另外實作原子扣款、機率判定、效果與通知。
"""
from __future__ import annotations

import json
from core.database import transaction
from modules.faction_action_settlement_plan import preview
from modules.faction_action_treasury_guard import inspect_proposal_treasury


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_action_execution_intents (
            guild_id TEXT NOT NULL,
            proposal_id INTEGER NOT NULL,
            policy_revision INTEGER NOT NULL,
            quoted_cost INTEGER NOT NULL CHECK(quoted_cost > 0),
            status TEXT NOT NULL DEFAULT 'blocked'
                CHECK(status IN ('blocked','cancelled')),
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, proposal_id)
        )''')


def prepare_intent(guild_id, proposal_id):
    """只會建立一次不可執行的意圖；重複呼叫回傳既存紀錄。

    注意：此階段沒有任何資金移動、預留、亂數或角色效果。
    """
    gid, pid = str(guild_id), int(proposal_id)
    initialize()
    with transaction() as db:
        prior = db.execute('''SELECT policy_revision,quoted_cost,status,report_json
            FROM faction_action_execution_intents WHERE guild_id=? AND proposal_id=?''',
            (gid,pid)).fetchone()
        if prior:
            return {'guild_id':gid,'proposal_id':pid,
                    'policy_revision':int(prior['policy_revision']),
                    'quoted_cost':int(prior['quoted_cost']),
                    'status':prior['status'], 'report':json.loads(prior['report_json']),
                    'created_new':False, 'execution_enabled':False}
    # These checks only produce a snapshot, NOT an authorization token.
    policy_report = preview(gid,pid)
    treasury = inspect_proposal_treasury(gid,pid)
    report = {'policy':policy_report, 'treasury':treasury,
              'reason':'正式執行器尚未實作；意圖不可兌換成扣款許可。'}
    with transaction() as db:
        # Conflict-safe insert; another concurrent worker can only create the same key once.
        db.execute('''INSERT OR IGNORE INTO faction_action_execution_intents
            (guild_id,proposal_id,policy_revision,quoted_cost,status,report_json)
            VALUES (?,?,?,?,'blocked',?)''',
            (gid,pid,int(policy_report['policy_revision']),
             int(treasury['quoted_cost']),json.dumps(report,ensure_ascii=False,sort_keys=True)))
        row=db.execute('''SELECT policy_revision,quoted_cost,status,report_json
            FROM faction_action_execution_intents WHERE guild_id=? AND proposal_id=?''',
            (gid,pid)).fetchone()
    return {'guild_id':gid,'proposal_id':pid,
            'policy_revision':int(row['policy_revision']),
            'quoted_cost':int(row['quoted_cost']), 'status':row['status'],
            'report':json.loads(row['report_json']),
            'created_new':row['report_json']==json.dumps(report,ensure_ascii=False,sort_keys=True),
            'execution_enabled':False}


def cancel_intent(guild_id, proposal_id):
    """管理服務用：取消意圖，不退費（本階段本來就不會扣款）。"""
    initialize()
    with transaction() as db:
        cur=db.execute('''UPDATE faction_action_execution_intents
           SET status='cancelled' WHERE guild_id=? AND proposal_id=? AND status='blocked' ''',
           (str(guild_id),int(proposal_id)))
        return cur.rowcount == 1
