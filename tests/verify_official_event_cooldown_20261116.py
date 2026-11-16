"""官方地點20%／12h已核准邏輯：離線驗證成功顯示才計冷卻。"""
from pathlib import Path
import subprocess,os,tempfile,sys
root=Path(__file__).resolve().parents[1]
code=r'''
from core.database import init_db,transaction
from core.official_location_events_v1 import load,roll_after_action,mark_displayed,setup
from core.action_execution import initialize
init_db();gid='1544051203493601380';loc='theater';chan='1548945111457603604';cid=1
with transaction() as db:
 db.execute('INSERT INTO guilds(guild_id) VALUES(?)',(gid,))
 db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES (?,?,?,'active')",(gid,'u1','A'))
 initialize(db)
 for n in range(1,4):
  db.execute("""INSERT INTO rp_location_action_events
   (guild_id,request_key,character_id,action_fingerprint,location_key,action_name,result_json)
   VALUES (?,?,?,?,?,?,?)""",(gid,f'action{n}',cid,'fp',loc,'工作','{}'))
d,groups=load();d['default_enabled']=True;d['trigger_policy']['chance_percent']=100;d['trigger_policy']['cooldown_hours']=12
for entry in groups[loc]:entry['enabled']=True
first=roll_after_action(gid,cid,loc,'action1',channel_id=chan)
assert first and first['event_id']
second=roll_after_action(gid,cid,loc,'action2',channel_id=chan)
assert second and second['event_id'] # 事件尚未顯示，不消耗冷卻
assert mark_displayed(gid,'action1',cid)
assert not mark_displayed(gid,'action1',cid)
third=roll_after_action(gid,cid,loc,'action3',channel_id=chan)
assert third is None # 已顯示：12小時全地點冷卻
print('PASS official-event first display cooldown and dedupe')
'''
with tempfile.TemporaryDirectory() as d:
 env=dict(os.environ,DATABASE_PATH=os.path.join(d,'test.sqlite3'),PYTHONPATH=str(root))
 r=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,capture_output=True,text=True)
 print(r.stdout,r.stderr)
 assert r.returncode==0
