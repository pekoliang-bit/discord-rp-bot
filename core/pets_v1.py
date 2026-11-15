
"""Pets V1: guild-isolated ownership, chips, care, missing notices and encounters.

Every write uses a SQLite transaction. Fees use the existing double-entry economy.
Prices, escape rates, critical timer and other undecided defaults are editable,
not asserted as prior approval. No auto-ownership of protected animals.
"""
from __future__ import annotations
import json
import random
import secrets
from datetime import datetime,timezone,timedelta
from core.database import transaction,get_connection,get_or_create_account
from core.economy import _payment
from core.world_interaction_rules import pet_rules

UTC=timezone.utc

def now():return datetime.now(UTC)
def iso(dt):return dt.isoformat()
def parse(s):return datetime.fromisoformat(s).astimezone(UTC)
def clamp(v,upper):return max(0,min(int(upper),int(v)))

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pets_v1 (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      main_character_id INTEGER NOT NULL, name TEXT NOT NULL,
      species TEXT NOT NULL, rarity TEXT NOT NULL DEFAULT 'common',
      chip TEXT NOT NULL UNIQUE, health INTEGER NOT NULL, bond INTEGER NOT NULL,
      state TEXT NOT NULL DEFAULT 'home', illness TEXT NOT NULL DEFAULT '',
      last_play TEXT NOT NULL,last_tick TEXT NOT NULL,last_inactive_tick TEXT,last_daily_bonus TEXT NOT NULL DEFAULT '',
      critical_since TEXT, missing_since TEXT, last_location TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK(state IN ('home','missing','critical','deceased')),
      UNIQUE(guild_id,chip))''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(rp_pets_v1)')}
    if 'last_inactive_tick' not in columns:
        db.execute('ALTER TABLE rp_pets_v1 ADD COLUMN last_inactive_tick TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_owners_v1 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,character_id INTEGER NOT NULL,
      is_primary INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,pet_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_consent_v1 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL, accepted_at TEXT NOT NULL,
      terms_version TEXT NOT NULL DEFAULT '1',PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_posters_v1 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,description TEXT NOT NULL,
      location TEXT NOT NULL,reward INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(guild_id,pet_id))''')
    poster_columns={r['name'] for r in db.execute('PRAGMA table_info(rp_pet_posters_v1)')}
    if 'revision' not in poster_columns:
        db.execute('ALTER TABLE rp_pet_posters_v1 ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_events_v1 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,pet_id INTEGER,
      actor_character_id INTEGER,event_type TEXT NOT NULL,detail_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_reward_days_v1 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,day TEXT NOT NULL,
      triggered INTEGER NOT NULL,character_id INTEGER NOT NULL,
      stat_name TEXT,amount INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,pet_id,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_encounters_v1 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
      actor_character_id INTEGER NOT NULL,kind TEXT NOT NULL,pet_id INTEGER,
      state TEXT NOT NULL DEFAULT 'open',created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')

def _actor(db,gid,cid,uid):
    row=db.execute("SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?",(gid,int(cid))).fetchone()
    if not row or row['status']!='active' or str(row['owner_id'])!=str(uid):
        raise PermissionError('只有本人已啟用角色可以進行寵物操作')

def _pet(db,gid,pid):
    row=db.execute('SELECT * FROM rp_pets_v1 WHERE guild_id=? AND id=?',(gid,int(pid))).fetchone()
    if not row:raise LookupError('找不到這隻寵物')
    return row

def _access(db,gid,pid,cid,primary=False):
    row=db.execute('SELECT is_primary FROM rp_pet_owners_v1 WHERE guild_id=? AND pet_id=? AND character_id=?',(gid,int(pid),int(cid))).fetchone()
    if not row or (primary and not row['is_primary']):raise PermissionError('你不是這隻寵物的授權飼主')

def _pay(db,gid,cid,amount,key,description):
    if amount<=0:return
    sender=get_or_create_account(db,gid,'character',cid,'wallet')
    receiver=get_or_create_account(db,gid,'system','pet_center','wallet')
    _payment(db,gid,key,sender,receiver,int(amount),description)

def accept_terms(guild_id,character_id,user_id):
    gid=str(guild_id)
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        db.execute('INSERT OR IGNORE INTO rp_pet_consent_v1 VALUES (?,?,?,?)',(gid,int(character_id),iso(now()),'1'))

def purchase(guild_id,character_id,user_id,name,species,*,rarity='common'):
    gid=str(guild_id);name=str(name).strip()[:50];species=str(species).strip()
    if not name:raise ValueError('寵物名字不能留空')
    rules=pet_rules(gid)
    if '保育' in species or species in rules['protected_species']:
        raise PermissionError('保育類動物不可買賣或私養，只能通報救援')
    if species not in rules['species']:raise ValueError('世界觀尚未上架此物種')
    if rarity not in rules['rarity_multipliers']:raise ValueError('該稀有度未上架')
    price=int(rules['species'][species])*int(rules['rarity_multipliers'][rarity])//100;timestamp=iso(now())
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        if not db.execute('SELECT 1 FROM rp_pet_consent_v1 WHERE guild_id=? AND character_id=?',(gid,int(character_id))).fetchone():
            raise PermissionError('第一次購買前必須閱讀並簽署寵物注意事項')
        chip='P-'+secrets.token_hex(12).upper()
        cur=db.execute('''INSERT INTO rp_pets_v1(guild_id,main_character_id,name,species,rarity,chip,health,bond,last_play,last_tick)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',(gid,int(character_id),name,species,rarity,chip,rules['health_max'],rules['bond_max'],timestamp,timestamp))
        pid=cur.lastrowid
        _pay(db,gid,character_id,price,f'pet:purchase:{gid}:{pid}',f'購買寵物 {name}')
        db.execute('INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',(gid,pid,int(character_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type) VALUES (?,?,?,?)',(gid,pid,int(character_id),'purchase'))
        return {'id':pid,'name':name,'chip':chip,'price':price}

def list_pets(guild_id,character_id):
    gid=str(guild_id)
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT p.* FROM rp_pets_v1 p JOIN rp_pet_owners_v1 o
         ON o.guild_id=p.guild_id AND o.pet_id=p.id WHERE p.guild_id=? AND o.character_id=? ORDER BY p.id''',(gid,int(character_id)))]

def add_coowner(guild_id,pet_id,character_id,user_id,other_character):
    gid=str(guild_id)
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id,primary=True)
        target=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND status='active'",(gid,int(other_character))).fetchone()
        if not target:raise LookupError('指定共同飼主必須是同群已啟用角色')
        db.execute('INSERT OR IGNORE INTO rp_pet_owners_v1 VALUES (?,?,?,0)',(gid,int(pet_id),int(other_character)))

def _tick(db,gid,pet,timestamp,rng):
    rules=pet_rules(gid)
    if pet['state']=='deceased':return
    last_daily=parse(pet['last_tick'])
    days=max(0,int((timestamp-last_daily).total_seconds()//86400))
    last_play=parse(pet['last_play'])
    last_inactive=parse(pet['last_inactive_tick']) if pet['last_inactive_tick'] else last_play
    last_inactive=max(last_inactive,last_play)
    missed=max(0,int((timestamp-last_inactive).total_seconds()//43200))
    if not days and not missed:return
    health=int(pet['health'])-days*rules['health_daily_loss']
    bond=int(pet['bond'])-days*rules['bond_daily_loss']-missed*rules['bond_inactive_12h_loss']
    if pet['illness']:
        health-=days*rules['untreated_24h_health_loss']
        bond-=days*rules['sick_daily_bond_loss']
    health=clamp(health,rules['health_max']);bond=clamp(bond,rules['bond_max'])
    critical=pet['critical_since'];state=pet['state']
    if health==0 and not critical:critical=iso(timestamp);state='critical' if state=='home' else state
    if health>0:critical=None;state='home' if state=='critical' else state
    if health==0 and critical and (timestamp-parse(critical)).total_seconds()>=rules['critical_hours']*3600:
        if rng.random()<0.25:state='deceased'
    next_daily=last_daily+timedelta(days=days)
    next_inactive=last_inactive+timedelta(hours=12*missed)
    db.execute('''UPDATE rp_pets_v1 SET health=?,bond=?,state=?,critical_since=?,last_tick=?,last_inactive_tick=?
        WHERE guild_id=? AND id=?''',
        (health,bond,state,critical,iso(next_daily),iso(next_inactive),gid,int(pet['id'])))

def daily(guild_id,*,timestamp=None,rng=None):
    gid=str(guild_id);timestamp=timestamp or now();rng=rng or random.SystemRandom()
    rules=pet_rules(gid);day=timestamp.date().isoformat();summary={'checked':0,'bonuses':0}
    with transaction() as db:
        initialize(db)
        pets=db.execute("SELECT * FROM rp_pets_v1 WHERE guild_id=? AND state!='deceased' ORDER BY id",(gid,)).fetchall()
        for old in pets:
            _tick(db,gid,old,timestamp,rng)
            summary['checked']+=1
            if db.execute('SELECT 1 FROM rp_pet_reward_days_v1 WHERE guild_id=? AND pet_id=? AND day=?',(gid,old['id'],day)).fetchone():continue
            fresh=_pet(db,gid,old['id']);triggered=0;stat=None;value=0
            if fresh['state']=='home' and not fresh['illness']:
                condition=None;damage=0
                if rng.randint(1,100)<=rules['disease_daily_chance']:
                    condition='sick';damage=rng.randint(rules['sick_health_loss_min'],rules['sick_health_loss_max'])
                elif rng.randint(1,100)<=rules['injury_daily_chance']:
                    condition='injured';damage=rng.randint(rules['injured_health_loss_min'],rules['injured_health_loss_max'])
                if condition:
                    new_health=clamp(int(fresh['health'])-damage,rules['health_max'])
                    critical=iso(timestamp) if new_health==0 else None
                    db.execute("UPDATE rp_pets_v1 SET illness=?,health=?,critical_since=?,state=? WHERE guild_id=? AND id=?",
                        (condition,new_health,critical,'critical' if new_health==0 else 'home',gid,int(old['id'])))
                    db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,event_type,detail_json) VALUES (?,?,?,?)',
                        (gid,int(old['id']),condition,json.dumps({'health_lost':damage})))
            fresh=_pet(db,gid,old['id'])
            if fresh['state']=='home' and int(fresh['bond'])<100:
                chance=rules['missing_risk_high'] if int(fresh['bond'])==0 else rules['missing_risk_mid'] if int(fresh['bond'])<50 else rules['missing_risk_low']
                if rng.randint(1,100)<=chance:
                    db.execute("UPDATE rp_pets_v1 SET state='missing',missing_since=? WHERE guild_id=? AND id=?",(iso(timestamp),gid,int(old['id'])))
                    fresh=_pet(db,gid,old['id'])
            if fresh['state']=='home' and rng.randint(1,100)<=rules['daily_bonus_chance']:
                available=[r['name'] for r in db.execute("SELECT name FROM attributes WHERE guild_id=? AND character_id=? AND name IN ('流量熱度','商業價值','路人緣','公關','容貌','才智','歌唱','舞蹈','演繹','主持','綜藝','個人才藝')",(gid,int(old['main_character_id'])))]
                if available:
                    stat=rng.choice(available);value=rng.randint(rules['daily_bonus_min'],rules['daily_bonus_max'])
                    db.execute('UPDATE attributes SET value=value+?,version=version+1 WHERE guild_id=? AND character_id=? AND name=?',(value,gid,int(old['main_character_id']),stat))
                    triggered=1;summary['bonuses']+=1
            db.execute('INSERT INTO rp_pet_reward_days_v1 VALUES (?,?,?,?,?,?,?)',
                (gid,int(old['id']),day,triggered,int(old['main_character_id']),stat,value))
    return summary

def care(guild_id,pet_id,character_id,user_id,action):
    gid=str(guild_id);rules=pet_rules(gid)
    actions={'play':('bond',rules['play_bond_gain']),'walk':('health',3),'feed':('health',4),
        'wrong_food':('health',-rules['wrong_food_health_loss']),
        'gift':('bond',rules['gift_bond_min']),'check':('health',0), 'treat':('health',25)}
    if action not in actions:raise ValueError('不支援的照顧方式')
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id)
        pet=_pet(db,gid,pet_id)
        if pet['state']=='deceased':raise ValueError('已死亡寵物不能再互動')
        if pet['state']=='missing':raise ValueError('寵物走失中，請先協尋')
        field,delta=actions[action]
        health, bond=int(pet['health']),int(pet['bond'])
        health=clamp(health+(delta if field=='health' else 0),rules['health_max'])
        bond=clamp(bond+(delta if field=='bond' else 0),rules['bond_max'])
        state=pet['state'];critical=pet['critical_since'];illness=pet['illness']
        if action=='treat':state='home';critical=None;illness=''
        if health==0 and not critical:state='critical';critical=iso(now())
        if action=='play':play=iso(now())
        else:play=pet['last_play']
        missing=False
        if action=='walk':
            risk=rules['walk_escape_chance']
            if bond==0:risk=max(risk,rules['missing_risk_high'])
            elif bond<50:risk=max(risk,rules['missing_risk_mid'])
            elif bond<100:risk=max(risk,rules['missing_risk_low'])
            if secrets.randbelow(100)<risk:
                state='missing';missing=True
        db.execute('''UPDATE rp_pets_v1 SET health=?,bond=?,state=?,critical_since=?,illness=?,last_play=?,last_inactive_tick=?,missing_since=?
            WHERE guild_id=? AND id=?''',(health,bond,state,critical,illness,play,play if action=='play' else pet['last_inactive_tick'],iso(now()) if missing else pet['missing_since'],gid,int(pet_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type) VALUES (?,?,?,?)',(gid,int(pet_id),int(character_id),action))
        return {'health':health,'bond':bond,'state':state}

def mark_missing(guild_id,pet_id,character_id,user_id,location):
    gid=str(guild_id)
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id)
        pet=_pet(db,gid,pet_id)
        if pet['state'] not in ('home','critical'):raise ValueError('此寵物狀態無法登記走失')
        db.execute("UPDATE rp_pets_v1 SET state='missing',missing_since=?,last_location=? WHERE guild_id=? AND id=?",(iso(now()),str(location)[:100],gid,int(pet_id)))

def publish_poster(guild_id,pet_id,character_id,user_id,description,location,reward):
    gid=str(guild_id);reward=int(reward)
    if not 0<=reward<=10**10:raise ValueError('懸賞金額不正確')
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id)
        pet=_pet(db,gid,pet_id)
        if pet['state']!='missing':raise ValueError('寵物未失蹤，無法發協尋')
        row=db.execute('SELECT state,revision FROM rp_pet_posters_v1 WHERE guild_id=? AND pet_id=?',(gid,int(pet_id))).fetchone()
        if row and row['state']=='open':raise ValueError('已有公開協尋案件，請不要重複發布')
        revision=int(row['revision'])+1 if row else 1
        if reward:
            sender=get_or_create_account(db,gid,'character',character_id,'wallet')
            escrow=get_or_create_account(db,gid,'escrow',f'pet:{pet_id}','escrow')
            _payment(db,gid,f'pet:poster:{gid}:{pet_id}:{revision}',sender,escrow,reward,'寵物協尋懸賞託管')
        db.execute('''INSERT INTO rp_pet_posters_v1
           (guild_id,pet_id,description,location,reward,state,created_at,revision)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(guild_id,pet_id) DO UPDATE SET description=excluded.description,
           location=excluded.location,reward=excluded.reward,state='open',
           created_at=excluded.created_at,revision=excluded.revision''',
           (gid,int(pet_id),str(description)[:1000],str(location)[:100],reward,'open',iso(now()),revision))
        return {'pet_id':pet_id,'reward':reward,'reach_multiplier':min(2.0,1+reward/30000)}

def public_posters(guild_id):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT p.name,p.species,p.chip,a.pet_id,a.location,a.description,a.reward
            FROM rp_pet_posters_v1 a JOIN rp_pets_v1 p ON p.guild_id=a.guild_id AND p.id=a.pet_id
            WHERE a.guild_id=? AND a.state='open' ORDER BY a.created_at DESC LIMIT 25''',(str(guild_id),))]

def rescue_encounter(guild_id,character_id,user_id,choice,*,rng=None):
    """A protected species can never be adopted. Missing/chipped animals need tracing."""
    rng=rng or random.SystemRandom();gid=str(guild_id)
    if choice not in ('leave','observe','shelter','hospital','report'):raise ValueError('未知處理方式')
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        missing=db.execute("SELECT id FROM rp_pets_v1 WHERE guild_id=? AND state='missing' ORDER BY RANDOM() LIMIT 1",(gid,)).fetchone()
        typ=rng.choice(['stray','stray','injured','protected','missing'] if missing else ['stray','stray','injured','protected'])
        pet_id=missing['id'] if typ=='missing' and missing else None
        positive=0;rumor=False;message=''
        if typ=='protected':
            if choice in ('hospital','report'):positive=1;message='已通報保育救援，可產生正面事件。'
            elif choice=='shelter':rumor=True;message='保育類不可私養，已記錄待調查負面事件。'
            else:message='發現保育類動物，請勿直接帶回家。'
        elif typ=='missing':message='疑似有晶片的走失寵物，請送醫掃晶片或提供協尋線索。'
        else:message='一般流浪／受傷動物，可協助救治；正式領養仍須另外確認身分。'
        db.execute('INSERT INTO rp_pet_encounters_v1(guild_id,actor_character_id,kind,pet_id) VALUES (?,?,?,?)',(gid,int(character_id),typ,pet_id))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',(gid,pet_id,int(character_id),'encounter',json.dumps({'kind':typ,'choice':choice,'positive':positive,'rumor':rumor},ensure_ascii=False)))
        return {'kind':typ,'message':message,'potential_positive':positive,'potential_rumor':rumor,'pet_id':pet_id}

def buy_service(guild_id,pet_id,character_id,user_id,item):
    gid=str(guild_id);rules=pet_rules(gid)
    if item not in rules['supplies']:raise ValueError('未上架的寵物商品')
    price=int(rules['supplies'][item])
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id)
        pet=_pet(db,gid,pet_id)
        if pet['state'] in ('missing','deceased'):raise ValueError('目前無法進行此操作')
        event=db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type) VALUES (?,?,?,?)',
            (gid,int(pet_id),int(character_id),'buy:'+item)).lastrowid
        _pay(db,gid,character_id,price,f'pet:service:{gid}:{event}',f'寵物{item}')
        health,bond=int(pet['health']),int(pet['bond'])
        if item=='飼料':health=clamp(health+5,rules['health_max'])
        if item in ('零食','玩具'):bond=clamp(bond+(rules['gift_bond_min'] if item=='零食' else rules['gift_bond_max']),rules['bond_max'])
        if item=='看醫生':health=clamp(health+25,rules['health_max'])
        state='home' if item=='看醫生' and health>0 else pet['state']
        db.execute('''UPDATE rp_pets_v1 SET health=?,bond=?,state=?,illness=?,critical_since=? WHERE guild_id=? AND id=?''',
            (health,bond,state,'' if item=='看醫生' else pet['illness'],None if item=='看醫生' else pet['critical_since'],gid,int(pet_id)))
        return {'price':price,'health':health,'bond':bond}

def begin_encounter(guild_id,character_id,user_id,*,rng=None):
    rng=rng or random.SystemRandom();gid=str(guild_id)
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        posters=db.execute("SELECT pet_id,reward FROM rp_pet_posters_v1 WHERE guild_id=? AND state='open'",(gid,)).fetchall()
        # Active posters + rewards increase visibility, but cannot guarantee rescue.
        chance=min(45,15+sum(min(5,int(x['reward'])//3000) for x in posters)) if posters else 0
        if posters and rng.randint(1,100)<=chance:
            selected=rng.choice(posters);kind='missing';pid=int(selected['pet_id'])
        else:
            kind=rng.choice(('stray','stray','injured','protected'));pid=None
        encounter=db.execute('INSERT INTO rp_pet_encounters_v1(guild_id,actor_character_id,kind,pet_id) VALUES (?,?,?,?)',
            (gid,int(character_id),kind,pid)).lastrowid
        labels={'stray':'一隻可能無主的流浪動物','injured':'一隻需要幫助的受傷動物',
          'protected':'一隻疑似保育類動物','missing':'一隻疑似失蹤的寵物'}
        return {'id':encounter,'kind':kind,'pet_id':pid,'description':labels[kind]}

def resolve_encounter(guild_id,encounter_id,character_id,user_id,choice):
    gid=str(guild_id)
    if choice not in ('leave','observe','shelter','hospital','report'):
        raise ValueError('未知處理方式')
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        row=db.execute("SELECT * FROM rp_pet_encounters_v1 WHERE guild_id=? AND id=? AND actor_character_id=? AND state='open'",
           (gid,int(encounter_id),int(character_id))).fetchone()
        if not row:raise ValueError('事件已完成，不能重複結算')
        kind=row['kind'];pid=row['pet_id']
        if kind=='protected':
            message='✅ 已通知保育單位，事件可作為正面救援紀錄。' if choice in ('report','hospital') else '⚠️ 私自收留保育動物已記錄違規風險。' if choice=='shelter' else '已留下觀察紀錄。'
        elif kind=='missing':
            message='已在動物醫院掃描晶片，待通知原飼主完成領回。' if choice=='hospital' else '這可能是其他飼主正在協尋的寵物。'
        else:message='救援紀錄已儲存。動物是否能領養，仍需確認晶片與物種。'
        db.execute("UPDATE rp_pet_encounters_v1 SET state='closed' WHERE id=?",(int(encounter_id),))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',
          (gid,pid,int(character_id),'encounter_resolved',json.dumps({'encounter_id':int(encounter_id),'kind':kind,'choice':choice},ensure_ascii=False)))
        return {'kind':kind,'choice':choice,'pet_id':pid,'message':message,
            'notify_owner':bool(pid and choice in ('hospital','report')),
            'protected_violation':kind=='protected' and choice=='shelter'}


def complete_rescue(guild_id,pet_id,*,authorized=False,rescuer_character=None):
    """Staff-confirmed chip / identity verification closes the missing case.
    Reward stays in real escrow until this single transaction commits.
    """
    if not authorized:raise PermissionError('尋回需管理員確認晶片與飼主身分')
    gid=str(guild_id)
    with transaction() as db:
        initialize(db)
        row=_pet(db,gid,pet_id)
        if row['state']!='missing':raise ValueError('寵物目前並非走失狀態')
        poster=db.execute("SELECT * FROM rp_pet_posters_v1 WHERE guild_id=? AND pet_id=? AND state='open'",(gid,int(pet_id))).fetchone()
        reward=int(poster['reward']) if poster else 0
        if rescuer_character is None:receiver_cid=int(row['main_character_id'])
        else:
            target=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND status='active'",(gid,int(rescuer_character))).fetchone()
            if not target:raise LookupError('協助者不是已啟用角色')
            receiver_cid=int(rescuer_character)
        if reward:
            escrow=get_or_create_account(db,gid,'escrow',f'pet:{pet_id}','escrow')
            receiver=get_or_create_account(db,gid,'character',receiver_cid,'wallet')
            _payment(db,gid,f'pet:return:{gid}:{pet_id}:{int(poster["revision"])}',escrow,receiver,reward,'協尋尋回懸賞／退款')
        db.execute("UPDATE rp_pets_v1 SET state='home',missing_since=NULL,last_location=NULL WHERE guild_id=? AND id=?",(gid,int(pet_id)))
        db.execute("UPDATE rp_pet_posters_v1 SET state='closed' WHERE guild_id=? AND pet_id=? AND state='open'",(gid,int(pet_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',
          (gid,int(pet_id),receiver_cid,'returned',json.dumps({'reward':reward,'rescuer':receiver_cid})))
        return {'pet_id':int(pet_id),'receiver':receiver_cid,'reward':reward}

def transfer_pet(guild_id,pet_id,character_id,user_id,new_owner):
    """Primary -> already approved co-owner, same permanent chip."""
    gid=str(guild_id)
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id);_access(db,gid,pet_id,character_id,primary=True)
        if int(character_id)==int(new_owner):raise ValueError('新飼主不可和原飼主相同')
        if not db.execute('SELECT 1 FROM rp_pet_owners_v1 WHERE guild_id=? AND pet_id=? AND character_id=?',(gid,int(pet_id),int(new_owner))).fetchone():
            raise ValueError('新主要飼主必須先登記為共同飼主')
        if not db.execute('SELECT 1 FROM rp_pet_consent_v1 WHERE guild_id=? AND character_id=?',(gid,int(new_owner))).fetchone():
            raise ValueError('新飼主尚未簽署寵物注意事項')
        old=_pet(db,gid,pet_id)
        if old['state'] in ('missing','deceased'):raise ValueError('走失或已死亡的寵物不可轉讓')
        db.execute('UPDATE rp_pet_owners_v1 SET is_primary=0 WHERE guild_id=? AND pet_id=?',(gid,int(pet_id)))
        db.execute('UPDATE rp_pet_owners_v1 SET is_primary=1 WHERE guild_id=? AND pet_id=? AND character_id=?',(gid,int(pet_id),int(new_owner)))
        db.execute('UPDATE rp_pets_v1 SET main_character_id=? WHERE guild_id=? AND id=?',(int(new_owner),gid,int(pet_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',
          (gid,int(pet_id),int(character_id),'transfer',json.dumps({'new_owner':int(new_owner)})))
        return old['chip']

def adopt_stray(guild_id,encounter_id,character_id,user_id,name,species):
    gid=str(guild_id);name=str(name).strip()[:50]
    if not name:raise ValueError('請為領養動物取名')
    rules=pet_rules(gid)
    if '保育' in species or species in rules['protected_species']:
        raise PermissionError('保育類動物禁止領養')
    if species not in rules['species']:raise ValueError('物種未在世界觀中核准')
    with transaction() as db:
        initialize(db);_actor(db,gid,character_id,user_id)
        if not db.execute('SELECT 1 FROM rp_pet_consent_v1 WHERE guild_id=? AND character_id=?',(gid,int(character_id))).fetchone():
            raise PermissionError('領養前須先簽署飼養注意事項')
        enc=db.execute("SELECT * FROM rp_pet_encounters_v1 WHERE guild_id=? AND id=? AND actor_character_id=? AND kind='stray' AND state='closed'",
          (gid,int(encounter_id),int(character_id))).fetchone()
        if not enc:raise ValueError('此事件沒有經確認可領養的無主動物')
        # Unknown species and chip cannot be silently treated as protected animal/another owner's pet.
        # Only a checked common stray may be adopted; explicit staff/vet validation is required.
        evidence=db.execute("SELECT detail_json FROM rp_pet_events_v1 WHERE guild_id=? AND actor_character_id=? AND event_type='encounter_resolved' ORDER BY id DESC LIMIT 100",(gid,int(character_id))).fetchall()
        if not any(json.loads(e[0]).get('encounter_id')==int(encounter_id) and json.loads(e[0]).get('choice')=='hospital' for e in evidence):
            raise ValueError('先由動物醫院掃描晶片並確認是無主流浪動物，才可領養')
        chip='P-'+secrets.token_hex(12).upper();timestamp=iso(now())
        cid=db.execute('''INSERT INTO rp_pets_v1(guild_id,main_character_id,name,species,chip,health,bond,last_play,last_tick)
          VALUES (?,?,?,?,?,?,?,?,?)''',(gid,int(character_id),name,species,chip,rules['health_max'],rules['bond_max'],timestamp,timestamp)).lastrowid
        db.execute('INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',(gid,cid,int(character_id)))
        db.execute("UPDATE rp_pet_encounters_v1 SET state='adopted' WHERE guild_id=? AND id=?",(gid,int(encounter_id)))
        return {'id':cid,'chip':chip}
