"""第26階段：結算結果唯讀／模擬儲存帳本，防重複鍵。不得當作已付款證明。"""
from __future__ import annotations
import json
from core.database import transaction

def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_action_result_ledger (
            guild_id TEXT NOT NULL, proposal_id INTEGER NOT NULL,
            result_type TEXT NOT NULL CHECK(result_type='simulation'),
            report_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,proposal_id))""")

def record_simulation(guild_id,proposal_id,report):
    if not isinstance(report,dict) or report.get('execution_enabled') is True:
        raise ValueError('只允許記錄明確未執行的模擬報告。')
    initialize()
    gid,pid=str(guild_id),int(proposal_id)
    serialized=json.dumps(report,ensure_ascii=False,sort_keys=True,allow_nan=False)
    with transaction() as db:
        db.execute("""INSERT OR IGNORE INTO faction_action_result_ledger
            (guild_id,proposal_id,result_type,report_json) VALUES (?,?,'simulation',?)""",(gid,pid,serialized))
        row=db.execute('SELECT report_json FROM faction_action_result_ledger WHERE guild_id=? AND proposal_id=?',(gid,pid)).fetchone()
    return json.loads(row['report_json'])

def read_simulation(guild_id,proposal_id):
    initialize()
    with transaction() as db:
        row=db.execute('SELECT report_json FROM faction_action_result_ledger WHERE guild_id=? AND proposal_id=?',(str(guild_id),int(proposal_id))).fetchone()
    return json.loads(row['report_json']) if row else None
