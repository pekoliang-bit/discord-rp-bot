
"""Current-580 history asset and NPC initial grant integration, isolated SQLite only."""
from __future__ import annotations
import json,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
A=ROOT/'assets/history_v23'
items=json.loads((A/'current_adopted_580.json').read_text(encoding='utf-8'))
assert len(items)==580 and len({x['asset_id'] for x in items})==580
assert all(x['approved'] and x['import_ready'] for x in items)
assert len([x for x in items if x['asset_id'].startswith('WN-')])==200
assert len([x for x in items if x['asset_id'].startswith('CN-')])==100
assert len([x for x in items if x['asset_id'].startswith('HB-')])==50
assert len([x for x in items if x['asset_id'].startswith('HE-')])==230
previous=json.loads((A/'verified_import_ready.json').read_text(encoding='utf-8'))
previous_map={x['asset_id']:x for x in previous}
for row in items:
    if row['asset_id'] in previous_map:
        assert row['title']==previous_map[row['asset_id']]['title']
assert all('new_fill_20261114'==x['meta'].get('source_version') for x in items if x['asset_id'] in {f'WN-{i:04}' for i in range(193,201)})

code=r'''
import sys,types,json
sys.modules['discord']=types.ModuleType('discord')
from core.database import init_db,transaction,get_connection
from core.history_v23 import put_batch,attempt_lock,init as history_init
from core.npc_initial_grants_v23 import configure,get_balance,award_in_transaction
from core.character_activation_v23 import activate_in_transaction
from core.action_execution import _spend_action_points
from core.permissions import InvalidState
from pathlib import Path
items=json.loads(Path('assets/history_v23/current_adopted_580.json').read_text(encoding='utf-8'))
init_db()
gid='1544051203493601380'
assert put_batch(gid,items)==580
assert put_batch(gid,items)==0
with get_connection() as db:
    assert db.execute("SELECT COUNT(*) FROM rp_history_assets_v23 WHERE pool='general'").fetchone()[0]==580
    assert db.execute("SELECT COUNT(*) FROM rp_history_assets_v23 WHERE pool='passerby'").fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM characters').fetchone()[0]==0
with transaction() as db:
    db.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)',(gid,))
    db.execute("""INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type,settings_json)
        VALUES (?,?,?,?,?,'approved','player_npc',?)""",(gid,'42','測試演員',25,'演員',json.dumps({'birthday':'04/18','experience_years':5})))
    cid=db.execute('SELECT id FROM characters').fetchone()[0]
    schema={'version':1,'general':['流量熱度','商業價值','路人緣','公關','影響指數'],
            'talent':['容貌','才智','演繹','舞蹈','歌唱','綜藝','主持','個人才藝']}
    db.execute('INSERT INTO module_records(guild_id,module,owner_id,record_type,data_json) VALUES (?,?,?,?,?)',
        (gid,'attributes','42','official_schema',json.dumps(schema,ensure_ascii=False)))
    for name in schema['general']+schema['talent']:
        db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',(gid,cid,name,100))
try:
    with transaction() as db:activate_in_transaction(db,gid,cid,'42',allowed_types=('player_npc',))
except InvalidState as ex:
    assert '初始金幣' in str(ex),str(ex)
else:raise AssertionError('沒有群主已核准金額卻允許啟用')
with get_connection() as db:
    assert db.execute('SELECT status FROM characters WHERE id=?',(cid,)).fetchone()['status']=='approved'
configure(gid,cid,'1',60000,6)
with transaction() as db:
    activate_in_transaction(db,gid,cid,'42',allowed_types=('player_npc',))
try:
    with transaction() as db:activate_in_transaction(db,gid,cid,'42',allowed_types=('player_npc',))
except InvalidState:pass
else:raise AssertionError('二次啟用未阻擋')
with transaction() as db:
    assert db.execute('SELECT status FROM characters WHERE id=?',(cid,)).fetchone()['status']=='active'
    assert get_balance(db,gid,cid)==6
    result=_spend_action_points(db,gid,cid,2,'2026-11-14T16:00:00+00:00')
    assert result=={'free':2,'paid':0},result
    assert get_balance(db,gid,cid)==4
    assert db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE unique_key=?",(f'npc-initial-v23:{gid}:{cid}',)).fetchone()[0]==1
    assert db.execute("""SELECT COALESCE(SUM(e.amount),0) FROM ledger_entries e JOIN accounts a
        ON a.guild_id=e.guild_id AND a.id=e.account_id
        WHERE a.guild_id=? AND a.owner_type='character' AND a.owner_id=? AND a.account_type='wallet'""",(gid,str(cid))).fetchone()[0]==60000
    assert db.execute('SELECT COUNT(*) FROM rp_character_free_action_events_v23').fetchone()[0]==1
# 群主直登的官方NPC 已 active；明確核准時立即入帳，重複核准必拒絕。
with transaction() as db:
    db.execute('INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type) VALUES (?,?,?,?,?,?,?)',
               (gid,'1','官方NPC',30,'製作人','active','official_npc'))
    official=db.execute('SELECT MAX(id) FROM characters').fetchone()[0]
configure(gid,official,'1',500,3)
with transaction() as db:
    assert get_balance(db,gid,official)==3
try:configure(gid,official,'1',500,3)
except ValueError:pass
else:raise AssertionError('官方NPC已入帳後不應允許重新核准')
print('PASS: 580 import, idempotence, NPC requires grant config, one-time 60000 gold and 6 actions, spend 2')
'''
with tempfile.TemporaryDirectory(prefix='history-npc-v23-') as td:
    env={**os.environ,'DATABASE_PATH':str(Path(td)/'test.sqlite')}
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True)
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)
