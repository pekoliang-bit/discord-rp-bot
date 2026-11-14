"""歷史五筆私人通知的隔離測試；不需要 Discord Token、不寫正式玩家資料。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = r'''
import json
import sys
import types
from core.database import init_db, transaction
from core.history_v23_delivery import read_locked_history,build_history_notice,queue_history_notice

init_db()
gid='100';cid=1
with transaction() as db:
    db.execute('INSERT INTO guilds(guild_id) VALUES (?)',(gid,))
    db.execute("INSERT INTO characters(guild_id,owner_id,name,status,character_type) VALUES (?,?,?,'approved','player_npc')",(gid,'42','通知測試'))

# 沒有正式 active 與五筆結果不能發任何通知。
assert read_locked_history(gid,cid) is None
assert build_history_notice(gid,cid) is None

with transaction() as db:
    db.execute("UPDATE characters SET status='active' WHERE id=?",(cid,))
    from core.history_v23 import init
    init(db)
    for slot,kind in enumerate(['work','endorsement','achievement','honor'],1):
        data={'title':f'測試內容{slot}', 'rarity':'R3'}
        if slot==1:
            data.update({'participation_role':'主演','character_name':'小艾',
                         'paired_hot_search':{'title':'@everyone 的新紀錄'}})
        db.execute('INSERT INTO rp_history_results_v23(guild_id,character_id,slot,kind,asset_id,snapshot_json) VALUES (?,?,?,?,?,?)',
            (gid,cid,slot,kind,f'WN-{slot:04d}',json.dumps(data,ensure_ascii=False)))
assert build_history_notice(gid,cid) is None
with transaction() as db:
    db.execute('INSERT INTO rp_history_results_v23(guild_id,character_id,slot,kind,asset_id,snapshot_json) VALUES (?,?,?,?,?,?)',
        (gid,cid,5,'work','WN-0020',json.dumps({'title':'第5份作品'},ensure_ascii=False)))
body=build_history_notice(gid,cid)
assert body and all(f'{i}.' in body for i in range(1,6))
assert '@everyone' not in body and '@\u200beveryone' in body
assert '出圈熱搜' in body and len(body)<=1800

# 通知以真正的 dedupe_key 寫入臨時資料庫；重送只會保留一筆。
module=types.ModuleType('modules.notifications')
def queue_notification(guild_id,kind,event_type,message,**kwargs):
    assert kind=='private' and event_type=='role' and len(message)<=1800
    with transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS test_outbox (guild_id TEXT,dedupe_key TEXT,body TEXT,PRIMARY KEY(guild_id,dedupe_key))")
        changed=db.execute('INSERT OR IGNORE INTO test_outbox VALUES (?,?,?)',
            (str(guild_id),kwargs['dedupe_key'],message)).rowcount
    return changed>0
module.queue_notification=queue_notification
sys.modules['modules.notifications']=module
assert queue_history_notice(gid,cid) is True
assert queue_history_notice(gid,cid) is False
with transaction() as db:
    assert db.execute('SELECT COUNT(*) FROM test_outbox').fetchone()[0]==1
    db.execute('UPDATE characters SET status=\'retired\' WHERE id=?',(cid,))
assert queue_history_notice(gid,cid) is False
print('PASS: only five locked slots notify, mentions sanitized, private notice once, no awards')
'''
with tempfile.TemporaryDirectory(prefix='v23-notice-') as temp:
    env={**os.environ,'DATABASE_PATH':str(Path(temp)/'isolated.sqlite3')}
    result=subprocess.run([sys.executable,'-c',PAYLOAD],cwd=ROOT,env=env,text=True,capture_output=True)
    if result.returncode:
        print(result.stdout, result.stderr)
        raise SystemExit(result.returncode)
    print(result.stdout.strip())
