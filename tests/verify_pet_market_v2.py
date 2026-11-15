"""Pet market and automatic action encounter regression; temp DB only."""
import os
import sys
import tempfile
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
code=r'''
import random
from core.database import init_db,transaction,get_or_create_account
from core.economy import _payment
from core import pet_market_v2 as m,pets_v1 as p
from core.world_interaction_rules import pet_rules
init_db();gid='777';uid='222'
with transaction() as db:
    db.execute('INSERT INTO guilds(guild_id) VALUES (?)',(gid,))
    db.execute("INSERT INTO characters(guild_id,owner_id,name,age,occupation,status) VALUES (?,?,?,?,?,'active')",(gid,uid,'飼主',20,'演員'))
    cid=db.execute('SELECT MAX(id) FROM characters').fetchone()[0]
    sys_acc=get_or_create_account(db,gid,'system','test','wallet')
    wallet=get_or_create_account(db,gid,'character',cid,'wallet')
    _payment(db,gid,'fund',sys_acc,wallet,1000000,'fund')
assert '貓' in m.species_list(gid)
assert m.preview(gid,cid)['ready']==False
m.select(gid,cid,uid,'breed','default:貓')
assert m.preview(gid,cid)['ready']==False
m.select(gid,cid,uid,'item','starter_feed')
assert m.preview(gid,cid)['ready']==False
m.select(gid,cid,uid,'item','starter_kit')
assert m.preview(gid,cid)['ready']==True
try:m.checkout(gid,cid,uid,'麻糬','a')
except PermissionError:pass
else:raise AssertionError('Missing consent permitted')
p.accept_terms(gid,cid,uid)
order=m.checkout(gid,cid,uid,'麻糬','a')
assert order['chip'].startswith('P-') and order['pet_id']
assert m.checkout(gid,cid,uid,'麻糬','a')==order
assert len(p.list_pets(gid,cid))==1
assert any(x['code']=='starter_feed' and x['quantity']==1 for x in m.inventory(gid,cid))
assert m.feed(gid,order['pet_id'],cid,uid)['health']>0
assert not any(x['code']=='starter_feed' and x['quantity']>0 for x in m.inventory(gid,cid))
try:m.feed(gid,order['pet_id'],cid,uid)
except ValueError:pass
else:raise AssertionError('Feed without inventory allowed')
m.select(gid,cid,uid,'item','starter_feed')
restock=m.checkout_supplies(gid,cid,uid,'supplies:1')
assert restock['pet_id'] is None and restock['total']>0
assert m.checkout_supplies(gid,cid,uid,'supplies:1')==restock
assert m.feed(gid,order['pet_id'],cid,uid)['health']>0
assert m.rename(gid,order['pet_id'],cid,uid,'咪咪')=='咪咪'
assert p.list_pets(gid,cid)[0]['name']=='咪咪'
# Invalid stock or delisting must block checkout without taking coins
m.manage(gid,'breed','test:cat','短毛貓',3200,species='貓',stock=1)
m.select(gid,cid,uid,'breed','test:cat')
m.select(gid,cid,uid,'item','starter_feed')
m.select(gid,cid,uid,'item','starter_kit')
m.manage(gid,'breed','test:cat','短毛貓',3200,species='貓',stock=1,active=False)
assert m.preview(gid,cid)['ready']==False
try:m.checkout(gid,cid,uid,'咪咪2','b')
except ValueError:pass
else:raise AssertionError('Unlisted breed allowed purchase')
m.manage(gid,'breed','test:cat','短毛貓',3200,species='貓',stock=1,active=True)
assert m.checkout(gid,cid,uid,'咪咪2','b')['pet_id']!=order['pet_id']
assert len(p.list_pets(gid,cid))==2
assert m.catalog(gid,'breed',species='貓',active_only=False)
# Roll no animal on 0% and once on 100% without manual encounter endpoint.
with transaction() as db:
    db.execute('CREATE TABLE IF NOT EXISTS world_configs(guild_id TEXT PRIMARY KEY,config_json TEXT NOT NULL)')
    db.execute('INSERT OR REPLACE INTO world_configs VALUES (?,?)',(gid,'{"pets":{"encounter_after_action_chance":100}}'))
a=m.action_encounter(gid,cid,uid,'action:1',rng=random.Random(11))
assert a is not None and m.encounter_detail(gid,a)['state']=='open'
assert m.action_encounter(gid,cid,uid,'action:1',rng=random.Random(22))==a
with transaction() as db:
    db.execute('UPDATE world_configs SET config_json=? WHERE guild_id=?',('{"pets":{"encounter_after_action_chance":0}}',gid))
assert m.action_encounter(gid,cid,uid,'action:2',rng=random.Random(11)) is None
assert m.action_encounter(gid,cid,uid,'action:2',rng=random.Random(11)) is None
print('PASS: catalog, required bundle, consent, one payment/chip, rename, delist, idempotent after-action encounter')
'''
with tempfile.TemporaryDirectory(prefix='petmarket_v2_') as folder:
    env={**os.environ,'DATABASE_PATH':str(Path(folder)/'test.sqlite3')}
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True)
    print(result.stdout)
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)
