"""新增規則回歸測試；全部使用獨立 DATABASE_PATH，不動玩家正式資料。"""
from __future__ import annotations
import tempfile,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
code=r'''
from core.database import init_db, transaction, get_or_create_account, account_balance
from core import beta_test_core as beta
from core.beta_sandbox_v1 import is_beta,simulate_location_action
from core.economy import _payment,EconomyError
from core.action_execution import execute_action
from core.bug_report_core_v2 import record_snapshot,reward_once,settle_pending_for_owner
init_db();gid='testguild';uid='u-beta'
beta.setup()
cid,ok=beta.claim(gid,uid)
assert ok
assert beta.is_beta_character is not None
with transaction() as db:
 assert is_beta(db,gid,cid)
 wallet=get_or_create_account(db,gid,'character',str(cid),'wallet')
 treasury=get_or_create_account(db,gid,'system','QA','treasury')
 try:_payment(db,gid,'bad-transfer',treasury,wallet,500)
 except EconomyError:pass
 else:raise AssertionError('正式金幣不得入測試角色')
assert beta.edit(gid,uid,'測試改名',24,'歌手','測試介紹')==cid
act={'name':'場景測試','category':'打工','approved':True,'action_points_cost':1,'reward_coins':500,'cost_coins':0,'stat_rewards':[]}
result=execute_action(gid,uid,cid,'venue',act,'qa:act:1')
assert result['test_only'] and result['action_points_spent']==1
assert execute_action(gid,uid,cid,'venue',act,'qa:act:1')['duplicated']
with transaction() as db:
 assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_location_action_events'").fetchone()
 assert db.execute('SELECT COUNT(*) FROM rp_beta_sandbox_events_v1').fetchone()[0]==1
 assert account_balance(db,gid,wallet)==0
record_snapshot(gid,'m1',uid,True)
assert reward_once(gid,'m1',uid,'owner','ack',False)['state']=='pending'
assert reward_once(gid,'m1',uid,'owner','done',False)['state']=='pending'
assert reward_once(gid,'m1',uid,'owner','ack',False) is None
with transaction() as db:
 pending=db.execute('SELECT amount,state FROM rp_bug_report_awards_v2 ORDER BY stage').fetchall()
 assert sorted([r['amount'] for r in pending])==[1000,1000]
assert beta.finish(gid,'owner')==1
with transaction() as db:
 assert db.execute('SELECT status FROM characters WHERE id=?',(cid,)).fetchone()['status']=='active'
 assert is_beta(db,gid,cid)
assert beta.edit(gid,uid,'保留測試',25,'演員','仍可測試')==cid
draft,created=beta.convert_to_draft(gid,uid)
assert created and draft != cid
with transaction() as db:
 assert db.execute('SELECT status FROM characters WHERE id=?',(draft,)).fetchone()['status']=='draft'
 db.execute('UPDATE characters SET status=? WHERE id=?',('active',draft))
 assert not is_beta(db,gid,draft)
assert len(settle_pending_for_owner(gid,uid))==2
assert len(settle_pending_for_owner(gid,uid))==0
with transaction() as db:
 official=get_or_create_account(db,gid,'character',str(draft),'wallet')
 assert account_balance(db,gid,official)==2000
 assert account_balance(db,gid,wallet)==0
 print('PASS beta lifecycle, sandbox, isolation, pending reports, frozen amounts')
'''
with tempfile.TemporaryDirectory() as folder:
 env=dict(os.environ,DATABASE_PATH=os.path.join(folder,'qa.sqlite3'),PYTHONPATH=str(root))
 result=subprocess.run([sys.executable,'-c',code],cwd=str(root),env=env,capture_output=True,text=True)
 print(result.stdout,result.stderr)
 assert result.returncode==0
