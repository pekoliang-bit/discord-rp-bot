
"""Pet market V2: selectable stock, required starter kit, atomic checkout and chip.

Pre-launch admin configuration is allowed; player operations remain launch gated
by the bot's interaction-wide gate. Unapproved catalog prices are provisional.
"""
from __future__ import annotations
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment
from core.world_interaction_rules import pet_rules
from core import pets_v1 as pets
from core import pet_catalog_v21 as v21


def setup(db, guild_id=None):
    pets.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_catalog_v2 (
      guild_id TEXT NOT NULL,kind TEXT NOT NULL,code TEXT NOT NULL,
      species TEXT NOT NULL DEFAULT '',label TEXT NOT NULL,price INTEGER NOT NULL,
      stock INTEGER NOT NULL DEFAULT -1,active INTEGER NOT NULL DEFAULT 1,
      basic INTEGER NOT NULL DEFAULT 0,description TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(guild_id,kind,code),CHECK(price>=0),CHECK(stock>=-1),
      CHECK(kind IN ('breed','item')))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_cart_v2 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
      kind TEXT NOT NULL,code TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(guild_id,character_id,kind,code),CHECK(quantity>0) )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_inventory_v2 (
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,code TEXT NOT NULL,
      quantity INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,character_id,code), CHECK(quantity>=0))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_orders_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
      character_id INTEGER NOT NULL,request_key TEXT NOT NULL,
      order_json TEXT NOT NULL,pet_id INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(guild_id,request_key))''')
    # 既有 V2 資料表原位升級，保留玩家購物車、訂單與庫存。
    columns={r[1] for r in db.execute('PRAGMA table_info(rp_pet_catalog_v2)')}
    for field,definition in {
        'category':"TEXT NOT NULL DEFAULT '日常'",'quality':"TEXT NOT NULL DEFAULT ''",
        'required_group':"TEXT NOT NULL DEFAULT ''",'hidden_health':'INTEGER NOT NULL DEFAULT 0',
        'hidden_bond':'INTEGER NOT NULL DEFAULT 0','permanent':'INTEGER NOT NULL DEFAULT 0',
        'breed_codes':"TEXT NOT NULL DEFAULT '[]'"}.items():
        if field not in columns: db.execute(f'ALTER TABLE rp_pet_catalog_v2 ADD COLUMN {field} {definition}')
    if guild_id is not None:
        guild_id=str(guild_id)
        rules=pet_rules(guild_id)
        # 歷史泛用品種保留但停止上架（不動玩家已購買資料）。
        db.execute("UPDATE rp_pet_catalog_v2 SET active=0 WHERE guild_id=? AND kind='breed' AND code LIKE 'default:%'",(guild_id,))
        for code,species,label,price,active in v21.BREEDS:
            db.execute("""INSERT OR IGNORE INTO rp_pet_catalog_v2
              (guild_id,kind,code,species,label,price,active,description)
              VALUES (?,'breed',?,?,?,?,?,'V2.1 候選價／未逐品種核准')""",
              (guild_id,code,species,label,price,active))
        for item in v21.item_templates():
            db.execute("""INSERT OR IGNORE INTO rp_pet_catalog_v2
              (guild_id,kind,code,species,label,price,active,basic,description,category,
               quality,required_group,hidden_health,hidden_bond,permanent,breed_codes)
              VALUES (?,'item',?,?,?,?,1,0,'V2.1 暫行用品數值',?,?,?,?,?,?,?)""",
              (guild_id,item['code'],item['species'],item['label'],item['price'],item['category'],
               item['quality'],item['group'],item['hidden_health'],item['hidden_bond'],int(item['permanent']),'[]'))
        # Legacy items are preserved for existing players; no generic required starter kit for V2.1.


def _row(db,gid,kind,code):
    row=db.execute('SELECT * FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind=? AND code=?',
                   (gid,kind,code)).fetchone()
    if not row:raise LookupError('商品不存在或尚未上架')
    return row


def catalog(guild_id,kind=None,species=None,active_only=True):
    gid=str(guild_id)
    with transaction() as db:
        setup(db,gid)
        query='SELECT * FROM rp_pet_catalog_v2 WHERE guild_id=?'; args=[gid]
        if kind:query+=' AND kind=?';args.append(kind)
        if species is not None:query+=' AND species=?';args.append(species)
        if active_only:query+=' AND active=1 AND stock!=0'
        query+=' ORDER BY kind,species,basic DESC,price,code'
        return [dict(r) for r in db.execute(query,args)]


def species_list(guild_id):
    return list(dict.fromkeys(r['species'] for r in catalog(guild_id,'breed') if r['species']))


def manage(guild_id,kind,code,label,price,*,species='',stock=-1,active=True,basic=False,description='',delete=False):
    gid=str(guild_id);kind=str(kind);code=str(code).strip();label=str(label).strip()
    if kind not in ('breed','item'):raise ValueError('商品類型必須為品種或用品')
    if not code or len(code)>80 or not label or len(label)>100:raise ValueError('商品代碼或名稱不正確')
    if not isinstance(price,int) or not 0<=price<=10**10 or not isinstance(stock,int) or stock< -1:
        raise ValueError('售價或庫存不正確')
    if kind=='breed' and not str(species).strip():raise ValueError('品種必須指定物種')
    rules=pet_rules(gid)
    if kind=='breed' and (str(species) in rules['protected_species'] or '保育' in str(species)):
        raise PermissionError('保育類物種不可販售')
    if delete:active=False  # soft unlisting prevents dangling cart references
    with transaction() as db:
        setup(db,gid)
        db.execute('''INSERT INTO rp_pet_catalog_v2(guild_id,kind,code,species,label,price,stock,active,basic,description)
          VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,kind,code) DO UPDATE SET
          species=excluded.species,label=excluded.label,price=excluded.price,stock=excluded.stock,
          active=excluded.active,basic=excluded.basic,description=excluded.description''',
          (gid,kind,code,str(species).strip() if kind=='breed' else '',label,price,stock,
           int(bool(active)),int(bool(basic) and kind=='item'),str(description)[:1000]))
    return {'kind':kind,'code':code,'active':bool(active),'price':price}


def _actor(db,gid,cid,uid):
    pets._actor(db,gid,cid,uid)


def select(guild_id,character_id,user_id,kind,code,quantity=1):
    gid=str(guild_id);cid=int(character_id);quantity=int(quantity)
    if kind not in ('breed','item') or not 1<=quantity<=99:raise ValueError('選購數量不正確')
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        item=_row(db,gid,kind,str(code))
        if not item['active'] or item['stock']==0:raise ValueError('商品已下架或售罄')
        if kind=='item' and item['required_group']:
            chosen=db.execute("SELECT a.species FROM rp_pet_cart_v2 c JOIN rp_pet_catalog_v2 a ON a.guild_id=c.guild_id AND a.kind=c.kind AND a.code=c.code WHERE c.guild_id=? AND c.character_id=? AND c.kind='breed'",(gid,cid)).fetchone()
            if chosen and item['species']!=chosen['species']:
                raise ValueError('商品不適用所選寵物物種')
            db.execute("""DELETE FROM rp_pet_cart_v2 WHERE guild_id=? AND character_id=? AND kind='item' AND code IN
              (SELECT code FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND species=? AND required_group=?)""",
              (gid,cid,gid,item['species'],item['required_group']))
        if kind=='breed':
            quantity=1
            db.execute("DELETE FROM rp_pet_cart_v2 WHERE guild_id=? AND character_id=? AND kind='breed'",(gid,cid))
        db.execute('''INSERT INTO rp_pet_cart_v2(guild_id,character_id,kind,code,quantity)
          VALUES (?,?,?,?,?) ON CONFLICT(guild_id,character_id,kind,code)
          DO UPDATE SET quantity=excluded.quantity''',(gid,cid,kind,str(code),quantity))
    return preview(gid,cid)


def remove(guild_id,character_id,user_id,kind,code):
    gid=str(guild_id);cid=int(character_id)
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        db.execute('DELETE FROM rp_pet_cart_v2 WHERE guild_id=? AND character_id=? AND kind=? AND code=?',(gid,cid,kind,str(code)))
    return preview(gid,cid)


def _preview(db,gid,cid):
    rows=db.execute('''SELECT c.kind,c.code,c.quantity,a.label,a.price,a.species,a.stock,a.active,a.basic
       FROM rp_pet_cart_v2 c JOIN rp_pet_catalog_v2 a ON
       c.guild_id=a.guild_id AND c.kind=a.kind AND c.code=a.code
       WHERE c.guild_id=? AND c.character_id=? ORDER BY c.kind,c.code''',(gid,cid)).fetchall()
    content=[dict(x) for x in rows]
    if any(x['active']==0 or x['stock']==0 or (x['stock']>=0 and x['quantity']>x['stock']) for x in content):
        valid=False
    else:valid=True
    breed=[x for x in content if x['kind']=='breed']
    chosen=breed[0] if len(breed)==1 else None
    if chosen and chosen['code'].startswith('v21:'):
        selected_breed=db.execute("SELECT label FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='breed' AND code=?",(gid,chosen['code'])).fetchone()
        breed_label=selected_breed['label'] if selected_breed else ''
        required=[g for g,_,req in v21.wizard_pages(chosen['species'],breed_label) if req]
        selected_groups=[]
        for x in content:
            if x['kind']!='item': continue
            meta=db.execute("SELECT species,required_group,breed_codes FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND code=?",(gid,x['code'])).fetchone()
            if meta and meta['species']==chosen['species'] and meta['required_group']:
                selected_groups.append(meta['required_group'])
        missing=[g for g in required if g not in selected_groups]
        for x in content:
            if x['kind']=='item':
                row=db.execute("SELECT species FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND code=?",(gid,x['code'])).fetchone()
                if row and row['species'] and row['species']!=chosen['species']: valid=False
    else:
        basics=[dict(x) for x in db.execute("SELECT * FROM rp_pet_catalog_v2 WHERE guild_id=? AND kind='item' AND basic=1 AND active=1",(gid,))]
        missing=[x['code'] for x in basics if not any(y['code']==x['code'] and y['quantity']>=1 for y in content)]
        for required_code in ('starter_feed','starter_kit'):
            if required_code not in [r['code'] for r in basics] or required_code not in [r['code'] for r in content if r['kind']=='item']:
                if required_code not in missing:missing.append(required_code)
        if not basics:missing.append('尚未上架任何基礎用品')
    return {'items':content,'total':sum(x['price']*x['quantity'] for x in content),
            'pet_selected':len(breed)==1,'missing_basics':missing,
            'ready':valid and len(breed)==1 and not missing}


def preview(guild_id,character_id):
    gid=str(guild_id)
    with transaction() as db:
        setup(db,gid)
        return _preview(db,gid,int(character_id))


def checkout(guild_id,character_id,user_id,pet_name,request_key):
    gid=str(guild_id);cid=int(character_id);pet_name=str(pet_name).strip()[:50]
    if not pet_name:raise ValueError('請先填寫寵物名字')
    if not str(request_key).strip():raise ValueError('缺少交易識別碼')
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        old=db.execute('SELECT order_json FROM rp_pet_orders_v2 WHERE guild_id=? AND request_key=?',(gid,str(request_key))).fetchone()
        if old:return json.loads(old['order_json'])
        if not db.execute('SELECT 1 FROM rp_pet_consent_v1 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone():
            raise PermissionError('請先閱讀並簽署完整飼主須知')
        state=_preview(db,gid,cid)
        if not state['ready']:raise ValueError('請先選擇寵物，並加入全部必備用品；確認商品未下架且庫存足夠')
        breed=next(x for x in state['items'] if x['kind']=='breed')
        rules=pet_rules(gid)
        if breed['species'] in rules['protected_species'] or '保育' in breed['species']:
            raise PermissionError('保育類動物不可交易')
        for x in state['items']:
            if x['stock']>=0:
                result=db.execute('''UPDATE rp_pet_catalog_v2 SET stock=stock-? WHERE guild_id=? AND kind=? AND code=? AND active=1 AND stock>=?''',
                  (x['quantity'],gid,x['kind'],x['code'],x['quantity']))
                if result.rowcount!=1:raise ValueError('商品庫存不足，請重新確認訂單')
        timestamp=datetime.now(timezone.utc).isoformat()
        chip='P-'+secrets.token_hex(12).upper()
        inserted=db.execute('''INSERT INTO rp_pets_v1(guild_id,main_character_id,name,species,rarity,chip,health,bond,last_play,last_tick)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',(gid,cid,pet_name,breed['species'],breed['code'],chip,
                 rules['health_max'],rules['bond_max'],timestamp,timestamp))
        pid=int(inserted.lastrowid)
        if state['total']:
            sender=get_or_create_account(db,gid,'character',cid,'wallet')
            receiver=get_or_create_account(db,gid,'system','pet_center','wallet')
            _payment(db,gid,'pet:checkout:'+gid+':'+str(request_key),sender,receiver,state['total'],'寵物＋必備飼養用品一次結帳與晶片登記')
        for item in state['items']:
            if item['kind']=='item':
                db.execute('''INSERT INTO rp_pet_inventory_v2(guild_id,character_id,code,quantity)
                    VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id,code)
                    DO UPDATE SET quantity=quantity+excluded.quantity''',
                    (gid,cid,item['code'],item['quantity']))
        db.execute('INSERT INTO rp_pet_owners_v1 VALUES (?,?,?,1)',(gid,pid,cid))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',
          (gid,pid,cid,'checkout',json.dumps(state,ensure_ascii=False)))
        result={'pet_id':pid,'name':pet_name,'species':breed['species'],'breed':breed['label'],
                'chip':chip,'total':state['total'],'items':state['items']}
        db.execute('INSERT INTO rp_pet_orders_v2(guild_id,character_id,request_key,order_json,pet_id) VALUES (?,?,?,?,?)',
                   (gid,cid,str(request_key),json.dumps(result,ensure_ascii=False),pid))
        db.execute('DELETE FROM rp_pet_cart_v2 WHERE guild_id=? AND character_id=?',(gid,cid))
        return result


def inventory(guild_id,character_id):
    with transaction() as db:
        setup(db,guild_id)
        return [dict(r) for r in db.execute('SELECT code,quantity FROM rp_pet_inventory_v2 WHERE guild_id=? AND character_id=? AND quantity>0',(str(guild_id),int(character_id)))]


def checkout_supplies(guild_id,character_id,user_id,request_key):
    # Restock supplies for existing pets without buying another animal/chip.
    gid=str(guild_id);cid=int(character_id)
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        old=db.execute('SELECT order_json FROM rp_pet_orders_v2 WHERE guild_id=? AND request_key=?',(gid,str(request_key))).fetchone()
        if old:return json.loads(old['order_json'])
        state=_preview(db,gid,cid)
        if not state['items'] or any(x['kind']=='breed' for x in state['items']):
            raise ValueError('用品補貨只能選擇飼料／用品；選了寵物請走晶片結帳流程')
        if any(not x['active'] or x['stock']==0 or (x['stock']>=0 and x['quantity']>x['stock']) for x in state['items']):
            raise ValueError('商品已下架或庫存不足')
        for x in state['items']:
            if x['stock']>=0:
                changed=db.execute('''UPDATE rp_pet_catalog_v2 SET stock=stock-? WHERE guild_id=? AND kind=? AND code=? AND active=1 AND stock>=?''',
                    (x['quantity'],gid,x['kind'],x['code'],x['quantity']))
                if changed.rowcount!=1:raise ValueError('庫存不足')
        if state['total']:
            sender=get_or_create_account(db,gid,'character',cid,'wallet')
            receiver=get_or_create_account(db,gid,'system','pet_center','wallet')
            _payment(db,gid,'pet:supplies:'+gid+':'+str(request_key),sender,receiver,state['total'],'寵物飼料／用品統一結帳')
        for x in state['items']:
            db.execute('''INSERT INTO rp_pet_inventory_v2(guild_id,character_id,code,quantity)
                    VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id,code)
                    DO UPDATE SET quantity=quantity+excluded.quantity''',
                    (gid,cid,x['code'],x['quantity']))
        result={'pet_id':None,'chip':None,'total':state['total'],'items':state['items']}
        db.execute('''INSERT INTO rp_pet_orders_v2(guild_id,character_id,request_key,order_json,pet_id)
                    VALUES (?,?,?,?,0)''',(gid,cid,str(request_key),json.dumps(result,ensure_ascii=False)))
        db.execute('DELETE FROM rp_pet_cart_v2 WHERE guild_id=? AND character_id=?',(gid,cid))
        return result


def feed(guild_id,pet_id,character_id,user_id):
    # Consume one previously purchased food unit, then restore pet health.
    gid=str(guild_id);cid=int(character_id)
    rules=pet_rules(gid)
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id);pets._access(db,gid,pet_id,cid)
        target=pets._pet(db,gid,pet_id)
        if target['state'] in ('missing','deceased'):
            raise ValueError('目前無法餵食這隻寵物')
        used=db.execute('''UPDATE rp_pet_inventory_v2 SET quantity=quantity-1
            WHERE guild_id=? AND character_id=? AND code='starter_feed' AND quantity>=1''',(gid,cid))
        if used.rowcount!=1:raise ValueError('飼料不足，請先到 /寵物中心 選購並結帳')
        new_health=pets.clamp(int(target['health'])+4,rules['health_max'])
        db.execute('UPDATE rp_pets_v1 SET health=? WHERE guild_id=? AND id=?',(new_health,gid,int(pet_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type) VALUES (?,?,?,?)',
                   (gid,int(pet_id),cid,'feed'))
        return {'health':new_health,'bond':int(target['bond']),'state':target['state']}


def rename(guild_id,pet_id,character_id,user_id,new_name):
    gid=str(guild_id);name=str(new_name).strip()[:50]
    if not name:raise ValueError('名字不能空白')
    with transaction() as db:
        setup(db,gid);_actor(db,gid,character_id,user_id)
        pets._access(db,gid,pet_id,character_id)
        p=pets._pet(db,gid,pet_id)
        if p['state']=='deceased':raise ValueError('無法更改已故寵物的名字')
        db.execute('UPDATE rp_pets_v1 SET name=? WHERE guild_id=? AND id=?',(name,gid,int(pet_id)))
        db.execute('INSERT INTO rp_pet_events_v1(guild_id,pet_id,actor_character_id,event_type,detail_json) VALUES (?,?,?,?,?)',
                   (gid,int(pet_id),int(character_id),'rename',json.dumps({'old':p['name'],'new':name},ensure_ascii=False)))
        return name


def action_encounter(guild_id,character_id,user_id,action_key,rng=None,location_key=None):
    """Roll *after successful /行動*. Idempotent per action transaction.

    Return None when no encounter. Never callable from a selectable /寵物中心 menu.
    """
    import random
    from core.pet_location_events_v21 import get_rules as get_location_rules
    gid=str(guild_id);cid=int(character_id)
    rules=pet_rules(gid);rng=rng or random.SystemRandom()
    local=get_location_rules(gid,location_key) if location_key is not None else None
    with transaction() as db:
        setup(db,gid);_actor(db,gid,cid,user_id)
        db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_action_rolls_v2 (
          guild_id TEXT NOT NULL,request_key TEXT NOT NULL,character_id INTEGER NOT NULL,
          encounter_id INTEGER,PRIMARY KEY(guild_id,request_key))''')
        prev=db.execute('SELECT encounter_id FROM rp_pet_action_rolls_v2 WHERE guild_id=? AND request_key=?',(gid,str(action_key))).fetchone()
        if prev:return int(prev['encounter_id']) if prev['encounter_id'] else None
        chance=max(0,min(100,int(local['chance'] if local else rules.get('encounter_after_action_chance',5))))
        encounter_id=None
        if rng.randint(1,100)<=chance:
            posters=db.execute("SELECT pet_id,reward FROM rp_pet_posters_v1 WHERE guild_id=? AND state='open'",(gid,)).fetchall()
            chance_missing=min(45,15+sum(min(5,int(x['reward'])//3000) for x in posters)) if posters else 0
            if posters and rng.randint(1,100)<=chance_missing:
                selected=rng.choice(posters);kind='missing';pid=int(selected['pet_id'])
            else:
                pool=local['pool'] if local else ['stray_cat','stray_dog','missing_pet','nothing']
                available=[x for x in pool if x not in ('nothing','missing_pet')]
                if not available: available=['stray_cat']
                chosen=rng.choice(available)
                kind='stray' if chosen in ('stray_cat','stray_dog') else 'injured'
                pid=None
            encounter_id=db.execute('INSERT INTO rp_pet_encounters_v1(guild_id,actor_character_id,kind,pet_id) VALUES (?,?,?,?)',
                (gid,cid,kind,pid)).lastrowid
        db.execute('INSERT INTO rp_pet_action_rolls_v2 VALUES (?,?,?,?)',(gid,str(action_key),cid,encounter_id))
        return encounter_id


def encounter_detail(guild_id,encounter_id):
    with get_connection() as db:
        row=db.execute('SELECT * FROM rp_pet_encounters_v1 WHERE guild_id=? AND id=?',(str(guild_id),int(encounter_id))).fetchone()
        return dict(row) if row else None


def wizard_items(guild_id, species, group, breed_code=None):
    """依頁面、物種列出商品，精確隱藏加成不回傳給 UI。"""
    if species not in v21.PAGES: return []
    rows=catalog(guild_id,'item',species)
    return [r for r in rows if r.get('required_group')==group]


def wizard_pages(guild_id,breed_code):
    row=next((r for r in catalog(guild_id,'breed') if r['code']==breed_code),None)
    if not row:raise LookupError('請先選擇已上架品種')
    return v21.wizard_pages(row['species'],row['label'])
