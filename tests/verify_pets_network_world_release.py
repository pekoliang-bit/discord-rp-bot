
"""Pet/network/world integration sanity tests using a temp sqlite database only."""
from __future__ import annotations
import ast,os,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

for path in (
    'core/world_interaction_rules.py',
    'core/network_reaction_rewards_v11.py',
    'core/pets_v1.py',
    'modules/world_pets_reactions_ui.py',
    'modules/pet_center.py',
    'modules/network_reaction_v11.py',
    'modules/content_builder.py',
    'modules/network_forum_ui.py',
    'modules/review_center.py',
    'core/official_commands.py',
    'modules/overview_catalog.py'
):
    ast.parse((ROOT/path).read_text(encoding='utf-8'))

code=r'''
import json,random
from datetime import datetime,timedelta,timezone
from core.database import init_db,transaction,get_connection,get_or_create_account
from core.world_interaction_rules import reaction_effects,reaction_rules,split_emojis,validate_reactions,pet_rules
from core import network_reaction_rewards_v11 as rw
from core import pets_v1 as pet,pet_agreement_v23 as agreement
from core.economy import _payment

init_db()
gid='123'
u='101'
v='102'

with transaction() as db:
 db.execute('INSERT OR IGNORE INTO guilds(guild_id) VALUES (?)',(gid,))
 db.execute(
  "INSERT INTO characters(guild_id,owner_id,name,age,occupation,status) VALUES (?,?,?,?,?,'active')",
  (gid,u,'飼主',20,'演員')
 )
 c1=db.execute('SELECT MAX(id) FROM characters').fetchone()[0]
 db.execute(
  "INSERT INTO characters(guild_id,owner_id,name,age,occupation,status) VALUES (?,?,?,?,?,'active')",
  (gid,v,'互動者',21,'網紅')
 )
 c2=db.execute('SELECT MAX(id) FROM characters').fetchone()[0]
 db.execute(
  'UPDATE characters SET public_code=? WHERE id=?',
  ('P-01-0418-01',c1)
 )
 db.execute(
  'UPDATE characters SET public_code=? WHERE id=?',
  ('P-01-0419-01',c2)
 )
 db.execute(
  'INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',
  (gid,c1,'流量熱度',100)
 )
 db.execute(
  'INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',
  (gid,c1,'路人緣',100)
 )
 db.execute(
  'INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',
  (gid,c1,'演繹',30)
 )
 sender=get_or_create_account(db,gid,'system','test_bank','wallet')
 receiver=get_or_create_account(db,gid,'character',c1,'wallet')
 _payment(db,gid,'test:fund',sender,receiver,1000000,'test fixture funding')
 db.execute(
  'CREATE TABLE rp_network_posts(guild_id TEXT,thread_id TEXT,account_id INTEGER,character_id INTEGER)'
 )
 db.execute(
  'INSERT INTO rp_network_posts VALUES (?,?,?,?)',
  (gid,'456',1,c1)
 )

assert reaction_effects(reaction_rules(gid),'🍓')=={'heat':1}
assert '❤️' in split_emojis('❤️😍')
assert 'fans' in reaction_effects(reaction_rules(gid),'❤️')

try:
 validate_reactions({'fans_down':'❤️'})
except ValueError:
 pass
else:
 raise AssertionError('same-metric opposite emoji conflict not blocked')

assert rw.add(gid,'456',u,'❤️')=='self_reaction'
roll=rw.add(gid,'456',v,'❤️',rng=random.Random(42))
assert 'fans' in roll
assert rw.add(gid,'456',v,'❤️')=='already'

rev=rw.withdraw(gid,'456',v,'❤️',actor_id=v,penalty=False)
assert rev['reverted']==roll and not rev['penalty']
assert rw.withdraw(gid,'456',v,'❤️',actor_id=v,penalty=False) is None
assert rw.add(gid,'456',v,'❤️')=='already'

roll=rw.add(gid,'456',v,'🍓',rng=random.Random(1))
assert roll=={'heat':1},roll

agreement.start(gid,c1,u)
for page in range(9):
 agreement.advance(gid,c1,u,page)

agreement.sign(gid,c1,u,'飼主','P-01-0418-01',10)
info=pet.purchase(gid,c1,u,'麻糬','貓')

assert info['chip'].startswith('P-')
assert pet.list_pets(gid,c1)[0]['name']=='麻糬'

pet.add_coowner(gid,info['id'],c1,u,c2)
assert pet.care(gid,info['id'],c2,v,'play')['bond']==pet_rules(gid)['bond_max']

try:
 pet.purchase(gid,c2,v,'未簽署','貓')
except PermissionError:
 pass
else:
 raise AssertionError('consent missing')

with transaction() as db:
 db.execute(
  "UPDATE rp_pets_v1 SET last_play=?,last_tick=?,last_inactive_tick=? WHERE id=?",
  ((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),)*3+(info['id'],)
 )

res=pet.daily(gid,rng=random.Random(100))
assert res['checked']==1

p=pet.list_pets(gid,c1)[0]
assert p['health']==pet_rules(gid)['health_max']-4
assert p['bond']<pet_rules(gid)['bond_max']
assert pet.daily(gid)['bonuses']==0

pet.mark_missing(gid,info['id'],c1,u,'中央公園')
post=pet.publish_poster(
 gid,info['id'],c1,u,'藍色項圈','中央公園',3000
)
assert post['reward']==3000 and pet.public_posters(gid)

try:
 pet.publish_poster(gid,info['id'],c1,u,'重複','中央公園',3000)
except ValueError:
 pass
else:
 raise AssertionError('duplicate poster allowed')

enc=pet.rescue_encounter(gid,c2,v,'report',rng=random.Random(1))
assert enc['kind'] in ('stray','injured','protected','missing')

agreement.start(gid,c2,v)
for page in range(9):
 agreement.advance(gid,c2,v,page)

agreement.sign(gid,c2,v,'互動者','P-01-0419-01',10)

closed=pet.complete_rescue(
 gid,info['id'],authorized=True,rescuer_character=c2
)
assert closed['reward']==3000
assert pet.list_pets(gid,c1)[0]['state']=='home'

try:
 pet.complete_rescue(gid,info['id'],authorized=True,rescuer_character=c2)
except ValueError:
 pass
else:
 raise AssertionError('Repeated rescue must not pay again')

chip=pet.transfer_pet(gid,info['id'],c1,u,c2)
assert chip==info['chip']
assert pet.list_pets(gid,c2)[0]['main_character_id']==c2

pet.mark_missing(gid,info['id'],c2,v,'機場')
post=pet.publish_poster(
 gid,info['id'],c2,v,'新走失','機場',100
)
assert post['reward']==100
assert pet.complete_rescue(
 gid,info['id'],authorized=True
)['reward']==100

with transaction() as db:
 db.execute(
  'CREATE TABLE world_configs(guild_id TEXT PRIMARY KEY,config_json TEXT NOT NULL)'
 )
 db.execute(
  'INSERT INTO world_configs VALUES (?,?)',
  (
   'other',
   json.dumps({
    'pets':{'health_max':200},
    'reactions':{'random_min':3,'random_max':4}
   })
  )
 )

assert pet_rules('other')['health_max']==200
assert pet_rules(gid)['health_max']==100
assert reaction_rules('other')['random_max']==4
assert reaction_rules(gid)['random_max']==10

print(
 'PASS: emoji classification/one-time roll/rollback, '
 'consent, ownership, wallet, decay, poster, rescue'
)
'''

with tempfile.TemporaryDirectory(prefix='flow-release-') as td:
    env={
        **os.environ,
        'DATABASE_PATH':str(Path(td)/'isolated.sqlite')
    }
    r=subprocess.run(
        [sys.executable,'-c',code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True
    )
    print(r.stdout)
    if r.returncode:
        print(r.stderr)
        raise SystemExit(r.returncode)
