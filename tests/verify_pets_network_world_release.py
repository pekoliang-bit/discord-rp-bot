"""Pet/network/world integration sanity tests using a temp sqlite database only\."""
from \_\_future\_\_ import annotations
import ast,os,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path&#40;\_\_file\_\_&#41;\.resolve&#40;&#41;\.parents&#91;1&#93;
for path in &#40;'core/world\_interaction\_rules\.py','core/network\_reaction\_rewards\_v11\.py',
 'core/pets\_v1\.py','modules/world\_pets\_reactions\_ui\.py','modules/pet\_center\.py',
 'modules/network\_reaction\_v11\.py','modules/content\_builder\.py','modules/network\_forum\_ui\.py',
 'modules/review\_center\.py','core/official\_commands\.py','modules/overview\_catalog\.py'&#41;:
    ast\.parse&#40;&#40;ROOT/path&#41;\.read\_text&#40;encoding='utf\-8'&#41;&#41;
code=r'''
import json,random
from datetime import datetime,timedelta,timezone
from core\.database import init\_db,transaction,get\_connection,get\_or\_create\_account
from core\.world\_interaction\_rules import reaction\_effects,reaction\_rules,split\_emojis,validate\_reactions,pet\_rules
from core import network\_reaction\_rewards\_v11 as rw
from core import pets\_v1 as pet,pet\_agreement\_v23 as agreement
from core\.economy import \_payment
init\_db&#40;&#41;;gid='123';u='101';v='102'
with transaction&#40;&#41; as db:
 db\.execute&#40;'INSERT OR IGNORE INTO guilds&#40;guild\_id&#41; VALUES &#40;?&#41;',&#40;gid,&#41;&#41;
 db\.execute&#40;"INSERT INTO characters&#40;guild\_id,owner\_id,name,age,occupation,status&#41; VALUES &#40;?,?,?,?,?,'active'&#41;",&#40;gid,u,'飼主',20,'演員'&#41;&#41;
 c1=db\.execute&#40;'SELECT MAX&#40;id&#41; FROM characters'&#41;\.fetchone&#40;&#41;&#91;0&#93;
 db\.execute&#40;"INSERT INTO characters&#40;guild\_id,owner\_id,name,age,occupation,status&#41; VALUES &#40;?,?,?,?,?,'active'&#41;",&#40;gid,v,'互動者',21,'網紅'&#41;&#41;
 c2=db\.execute&#40;'SELECT MAX&#40;id&#41; FROM characters'&#41;\.fetchone&#40;&#41;&#91;0&#93;
 db\.execute&#40;'UPDATE characters SET public\_code=? WHERE id=?',&#40;'P\-01\-0418\-01',c1&#41;&#41;
 db\.execute&#40;'UPDATE characters SET public\_code=? WHERE id=?',&#40;'P\-01\-0419\-01',c2&#41;&#41;
 db\.execute&#40;'INSERT INTO attributes&#40;guild\_id,character\_id,name,value&#41; VALUES &#40;?,?,?,?&#41;',&#40;gid,c1,'流量熱度',100&#41;&#41;
 db\.execute&#40;'INSERT INTO attributes&#40;guild\_id,character\_id,name,value&#41; VALUES &#40;?,?,?,?&#41;',&#40;gid,c1,'路人緣',100&#41;&#41;
 db\.execute&#40;'INSERT INTO attributes&#40;guild\_id,character\_id,name,value&#41; VALUES &#40;?,?,?,?&#41;',&#40;gid,c1,'演繹',30&#41;&#41;
 sender=get\_or\_create\_account&#40;db,gid,'system','test\_bank','wallet'&#41;
 receiver=get\_or\_create\_account&#40;db,gid,'character',c1,'wallet'&#41;
 \_payment&#40;db,gid,'test:fund',sender,receiver,1000000,'test fixture funding'&#41;
 db\.execute&#40;'INSERT INTO rp\_network\_posts&#40;guild\_id,thread\_id,account\_id,character\_id&#41; VALUES &#40;?,?,?,?&#41;',&#40;gid,'456',1,c1&#41;&#41; if False else None
 db\.execute&#40;"CREATE TABLE rp\_network\_posts&#40;guild\_id TEXT,thread\_id TEXT,account\_id INTEGER,character\_id INTEGER&#41;"&#41;
 db\.execute&#40;'INSERT INTO rp\_network\_posts VALUES &#40;?,?,?,?&#41;',&#40;gid,'456',1,c1&#41;&#41;
assert reaction\_effects&#40;reaction\_rules&#40;gid&#41;,'🍓'&#41;==\{'heat':1\}
assert '❤️' in split\_emojis&#40;'❤️😍'&#41;
assert 'fans' in reaction\_effects&#40;reaction\_rules&#40;gid&#41;,'❤️'&#41;
try:validate\_reactions&#40;\{'fans\_down':'❤️'\}&#41;
except ValueError:pass
else:raise AssertionError&#40;'same\-metric opposite emoji conflict not blocked'&#41;
assert rw\.add&#40;gid,'456',u,'❤️'&#41;=='self\_reaction'
roll=rw\.add&#40;gid,'456',v,'❤️',rng=random\.Random&#40;42&#41;&#41;
assert 'fans' in roll
assert rw\.add&#40;gid,'456',v,'❤️'&#41;=='already'
rev=rw\.withdraw&#40;gid,'456',v,'❤️',actor\_id=v,penalty=False&#41;
assert rev&#91;'reverted'&#93;==roll and not rev&#91;'penalty'&#93;
assert rw\.withdraw&#40;gid,'456',v,'❤️',actor\_id=v,penalty=False&#41; is None
assert rw\.add&#40;gid,'456',v,'❤️'&#41;=='already'
roll=rw\.add&#40;gid,'456',v,'🍓',rng=random\.Random&#40;1&#41;&#41;
assert roll==\{'heat':1\},roll
agreement\.start&#40;gid,c1,u&#41;
for page in range&#40;9&#41;:agreement\.advance&#40;gid,c1,u,page&#41;
agreement\.sign&#40;gid,c1,u,'飼主','P\-01\-0418\-01',10&#41;
info=pet\.purchase&#40;gid,c1,u,'麻糬','貓'&#41;
assert info&#91;'chip'&#93;\.startswith&#40;'P\-'&#41;
assert pet\.list\_pets&#40;gid,c1&#41;&#91;0&#93;&#91;'name'&#93;=='麻糬'
pet\.add\_coowner&#40;gid,info&#91;'id'&#93;,c1,u,c2&#41;
assert pet\.care&#40;gid,info&#91;'id'&#93;,c2,v,'play'&#41;&#91;'bond'&#93;==pet\_rules&#40;gid&#41;&#91;'bond\_max'&#93;
try:pet\.purchase&#40;gid,c2,v,'未簽署','貓'&#41;
except PermissionError:pass
else:raise AssertionError&#40;'consent missing'&#41;
with transaction&#40;&#41; as db:
 db\.execute&#40;"UPDATE rp\_pets\_v1 SET last\_play=?,last\_tick=?,last\_inactive\_tick=? WHERE id=?",
   &#40;&#40;datetime\.now&#40;timezone\.utc&#41;\-timedelta&#40;days=2&#41;&#41;\.isoformat&#40;&#41;,&#41;\*3\+&#40;info&#91;'id'&#93;,&#41;&#41;
res=pet\.daily&#40;gid,rng=random\.Random&#40;100&#41;&#41;
assert res&#91;'checked'&#93;==1
p=pet\.list\_pets&#40;gid,c1&#41;&#91;0&#93;
assert p&#91;'health'&#93;==pet\_rules&#40;gid&#41;&#91;'health\_max'&#93;\-4
assert p&#91;'bond'&#93;<pet\_rules&#40;gid&#41;&#91;'bond\_max'&#93;
assert pet\.daily&#40;gid&#41;&#91;'bonuses'&#93;==0
pet\.mark\_missing&#40;gid,info&#91;'id'&#93;,c1,u,'中央公園'&#41;
post=pet\.publish\_poster&#40;gid,info&#91;'id'&#93;,c1,u,'藍色項圈','中央公園',3000&#41;
assert post&#91;'reward'&#93;==3000 and pet\.public\_posters&#40;gid&#41;
try:pet\.publish\_poster&#40;gid,info&#91;'id'&#93;,c1,u,'重複','中央公園',3000&#41;
except ValueError:pass
else:raise AssertionError&#40;'duplicate poster allowed'&#41;
enc=pet\.rescue\_encounter&#40;gid,c2,v,'report',rng=random\.Random&#40;1&#41;&#41;
assert enc&#91;'kind'&#93; in &#40;'stray','injured','protected','missing'&#41;
agreement\.start&#40;gid,c2,v&#41;
for page in range&#40;9&#41;:agreement\.advance&#40;gid,c2,v,page&#41;
agreement\.sign&#40;gid,c2,v,'互動者','P\-01\-0419\-01',10&#41;
closed=pet\.complete\_rescue&#40;gid,info&#91;'id'&#93;,authorized=True,rescuer\_character=c2&#41;
assert closed&#91;'reward'&#93;==3000
assert pet\.list\_pets&#40;gid,c1&#41;&#91;0&#93;&#91;'state'&#93;=='home'
try:pet\.complete\_rescue&#40;gid,info&#91;'id'&#93;,authorized=True,rescuer\_character=c2&#41;
except ValueError:pass
else:raise AssertionError&#40;'Repeated rescue must not pay again'&#41;
chip=pet\.transfer\_pet&#40;gid,info&#91;'id'&#93;,c1,u,c2&#41;
assert chip==info&#91;'chip'&#93;
assert pet\.list\_pets&#40;gid,c2&#41;&#91;0&#93;&#91;'main\_character\_id'&#93;==c2
pet\.mark\_missing&#40;gid,info&#91;'id'&#93;,c2,v,'機場'&#41;
post=pet\.publish\_poster&#40;gid,info&#91;'id'&#93;,c2,v,'新走失','機場',100&#41;
assert post&#91;'reward'&#93;==100
assert pet\.complete\_rescue&#40;gid,info&#91;'id'&#93;,authorized=True&#41;&#91;'reward'&#93;==100
\# Guild\-scoped world overrides must not mutate globally shared defaults\.
with transaction&#40;&#41; as db:
 db\.execute&#40;'CREATE TABLE world\_configs&#40;guild\_id TEXT PRIMARY KEY,config\_json TEXT NOT NULL&#41;'&#41;
 db\.execute&#40;'INSERT INTO world\_configs VALUES &#40;?,?&#41;',&#40;'other',json\.dumps&#40;\{'pets':\{'health\_max':200\},'reactions':\{'random\_min':3,'random\_max':4\}\}&#41;&#41;&#41;
assert pet\_rules&#40;'other'&#41;&#91;'health\_max'&#93;==200
assert pet\_rules&#40;gid&#41;&#91;'health\_max'&#93;==100
assert reaction\_rules&#40;'other'&#41;&#91;'random\_max'&#93;==4
assert reaction\_rules&#40;gid&#41;&#91;'random\_max'&#93;==10
print&#40;'PASS: emoji classification/one\-time roll/rollback, consent, ownership, wallet, decay, poster, rescue'&#41;
'''
with tempfile\.TemporaryDirectory&#40;prefix='flow\-release\-'&#41; as td:
    env=\{\*\*os\.environ,'DATABASE\_PATH':str&#40;Path&#40;td&#41;/'isolated\.sqlite'&#41;\}
    r=subprocess\.run&#40;&#91;sys\.executable,'\-c',code&#93;,cwd=ROOT,env=env,capture\_output=True,text=True&#41;
    print&#40;r\.stdout&#41;
    if r\.returncode:print&#40;r\.stderr&#41;;raise SystemExit&#40;r\.returncode&#41;
