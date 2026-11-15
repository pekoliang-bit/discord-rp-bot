"""V2.3 醫療／電子簽署獨立資料庫驗證。"""
import os,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
fd,path=tempfile.mkstemp(suffix='.db');os.close(fd)
try:
 import core.config as conf
 conf.DATABASE_PATH=path
 from core.database import init_db,transaction,get_or_create_account
 from core.economy import _payment
 from core import pet_medical_v23 as m,pet_agreement_v23 as a,pets_v1 as p
 init_db();gid='v23g';uid='12345'
 with transaction() as db:
  db.execute('INSERT INTO guilds(guild_id,name) VALUES (?,?)',(gid,'test'))
  cid=db.execute("INSERT INTO characters(guild_id,owner_id,name,status,public_code) VALUES (?,?,?,'active',?)",(gid,uid,'米歇爾','P-01-0418-01')).lastrowid
  db.execute('INSERT INTO rp_pet_consent_v1(guild_id,character_id,accepted_at,terms_version) VALUES (?,?,?,?)',(gid,cid,datetime.now(timezone.utc).isoformat(),'1')) if False else None
  p.initialize(db)
  t=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
  pid=db.execute('''INSERT INTO rp_pets_v1(guild_id,main_character_id,name,species,rarity,chip,health,bond,last_play,last_tick)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',(gid,cid,'布丁','狗','common','CHIPTEST',70,150,t,t)).lastrowid
  db.execute('INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',(gid,pid,cid))
  sysacc=get_or_create_account(db,gid,'system','test','wallet')
  wallet=get_or_create_account(db,gid,'character',cid,'wallet')
  _payment(db,gid,'fund-medical',sysacc,wallet,1000000,'fund')
  m.register_first_check(db,gid,pid,datetime.now(timezone.utc)-timedelta(days=10))
 a.start(gid,cid,uid)
 try:a.sign(gid,cid,uid,'米歇爾','P-01-0418-01',10);raise AssertionError('skipped pages')
 except PermissionError:pass
 for page in range(9):a.advance(gid,cid,uid,page)
 try:a.sign(gid,cid,uid,'米歇爾','WRONG',10);raise AssertionError('bad identity')
 except PermissionError:pass
 signed=a.sign(gid,cid,uid,'米歇爾','P-01-0418-01',10)
 assert signed['version']=='2.3'
 assert m.quote(['first_followup'],free_first=True)['total']==0
 result=m.pay(gid,pid,cid,uid,['first_followup'],'idempotent-check')
 assert result['total']==0
 assert m.first_check(gid,pid)['completed_at']
 assert m.pay(gid,pid,cid,uid,['first_followup'],'idempotent-check')==result
 case=m.open_case(gid,pid,cid,uid,'illness',2)
 assert m.open_case(gid,pid,cid,uid,'illness',2)==case
 assert m.quote(['illness_2','medication'])['total']==30000
 try:m.quote(['injury_3','fracture_fix']);raise AssertionError('double charge')
 except ValueError:pass
 assert m.quote(['ward_basic'])['total']==10000
 charged=m.pay(gid,pid,cid,uid,['illness_2'],'paid-treatment',case_id=case)
 assert charged['total']==25000
 assert m.pay(gid,pid,cid,uid,['illness_2'],'paid-treatment',case_id=case)==charged
 stay=m.pay(gid,pid,cid,uid,['ward_basic'],'paid-stay',case_id=case,ward_days=3)
 assert stay['total']==30000
 with transaction() as db:
  assert db.execute('SELECT count(*) FROM rp_pet_medical_stays_v23').fetchone()[0]==1
  assert db.execute('SELECT illness FROM rp_pets_v1 WHERE id=?',(pid,)).fetchone()[0]==''
 assert m.settle_recovery(gid,pid)==0
 print('PASS: V2.3 signatures, free first followup, price lists, duplicate case, duplicate invoice')
finally:
 try:os.unlink(path)
 except OSError:pass
