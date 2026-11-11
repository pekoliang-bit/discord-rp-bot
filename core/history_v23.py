"""歷史履歷 V2.3：隔離素材、稽核與已核准後的歷史內容鎖定。

注意：本模組不發放任何數值或金錢；未完成素材庫不可虛構補齊。
"""
from __future__ import annotations
import json
import random
import re
from core.database import transaction, get_connection

CATEGORIES = ('WN', 'CN', 'HB', 'HE')
POOLS = ('general', 'passerby')
RARITIES = ('R1','R2','R3','R4','R5')
WEIGHTS = ((50,30,14,5,1),(43,31,17,7,2),(34,30,21,12,3),(27,27,24,16,6))


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_history_assets_v23 (
        guild_id TEXT NOT NULL, pool TEXT NOT NULL, asset_id TEXT NOT NULL,
        category TEXT NOT NULL, title TEXT NOT NULL, tier TEXT NOT NULL DEFAULT '',
        rarity TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
        approved INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,pool,asset_id),
        CHECK(pool IN ('general','passerby')),
        CHECK(category IN ('WN','CN','HB','HE'))
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_history_results_v23 (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        slot INTEGER NOT NULL, kind TEXT NOT NULL, asset_id TEXT NOT NULL,
        snapshot_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,character_id,slot), CHECK(slot BETWEEN 1 AND 5)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_history_works_v23 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
        work_type TEXT NOT NULL,normalized_title TEXT NOT NULL,title TEXT NOT NULL,
        UNIQUE(guild_id,work_type,normalized_title)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_history_participants_v23 (
        guild_id TEXT NOT NULL,work_id INTEGER NOT NULL,character_id INTEGER NOT NULL,
        seat TEXT NOT NULL,role_name TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(guild_id,work_id,character_id),
        UNIQUE(guild_id,work_id,seat)
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_history_pending_v23 (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        reason TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,character_id)
    )''')


def put(gid,pool,asset_id,title,tier='',rarity='',meta=None,approved=False):
    pool=pool.strip();asset_id=asset_id.strip().upper();category=asset_id.split('-',1)[0]
    if pool not in POOLS or category not in CATEGORIES or not re.fullmatch(r'(WN|CN|HB|HE)-\d{4}',asset_id):
        raise ValueError('素材池或編號無效，請使用 WN/CN/HB/HE-0001 格式。')
    if not title.strip():raise ValueError('素材名稱不得留白。')
    if rarity and rarity not in RARITIES:raise ValueError('稀有度必須 R1～R5。')
    with transaction() as db:
        init(db)
        old=db.execute('SELECT revision,approved FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=?', (str(gid),pool,asset_id)).fetchone()
        if old and old['approved'] and not approved:
            raise ValueError('已核准素材只能由群主重新核准版本，不可直接覆蓋。')
        db.execute('''INSERT INTO rp_history_assets_v23(guild_id,pool,asset_id,category,title,tier,rarity,metadata_json,approved,enabled,revision)
          VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,pool,asset_id) DO UPDATE SET
          title=excluded.title,tier=excluded.tier,rarity=excluded.rarity,metadata_json=excluded.metadata_json,
          approved=excluded.approved,enabled=excluded.enabled,revision=rp_history_assets_v23.revision+1''',
          (str(gid),pool,asset_id,category,title.strip(),tier,rarity,json.dumps(meta or {},ensure_ascii=False),int(approved),int(approved), (old['revision']+1 if old else 1)))


def disable(gid,pool,asset_id,enabled):
    with transaction() as db:
        init(db)
        row=db.execute('UPDATE rp_history_assets_v23 SET enabled=? WHERE guild_id=? AND pool=? AND asset_id=? AND approved=1',(int(enabled),str(gid),pool,asset_id)).rowcount
        if not row:raise ValueError('沒有已核准的對應素材。')


def stats(gid,pool):
    with get_connection() as db:
        init(db)
        return [dict(r) for r in db.execute('''SELECT category,COUNT(*) AS total,SUM(approved) AS approved,SUM(enabled) AS enabled
             FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? GROUP BY category''',(str(gid),pool))]


def attempt_lock(gid,cid):
    """僅在 active 後嘗試五筆原子保存；素材不足時不建立半套歷史。

    作品池必須有職業匹配的 WN；角色名只在需要劇中角色時使用。
    即使 HE 素材不足也不假造內容。留待官方正式內容匯入後重試。
    """
    gid=str(gid);cid=int(cid)
    with transaction() as db:
        init(db)
        c=db.execute('SELECT character_type,occupation,status,settings_json FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if not c or c['status']!='active' or c['character_type'] not in ('player','player_npc','official_npc'):
            return 'not_active'
        old=db.execute('SELECT COUNT(*) AS n FROM rp_history_results_v23 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()['n']
        if old==5:return 'already'
        if old:raise ValueError('已有不完整的鎖定資料，需要人工核對，禁止重骰。')
        role=c['character_type'];occupation=c['occupation'] or ''
        settings=json.loads(c['settings_json'] or '{}')
        years=settings.get('career_years',settings.get('出道資歷',0))
        try:years=max(0,int(years))
        except (ValueError,TypeError):years=0
        weights=WEIGHTS[0 if years<=1 else 1 if years<=4 else 2 if years<=9 else 3]
        def pool(cat,extra=''):
            sql="SELECT * FROM rp_history_assets_v23 WHERE guild_id=? AND pool='general' AND category=? AND approved=1 AND enabled=1 "+extra
            return [dict(r) for r in db.execute(sql,(gid,cat))]
        works=pool('WN'); brands=pool('HE',"AND asset_id BETWEEN 'HE-0151' AND 'HE-0230'")
        events=pool('HE',"AND asset_id BETWEEN 'HE-0001' AND 'HE-0150'")
        works=[w for w in works if not json.loads(w['metadata_json'] or '{}').get('occupations') or occupation in json.loads(w['metadata_json'] or '{}').get('occupations',[])]
        maxb=3 if role=='player' else 5
        brands=[b for b in brands if b['tier'] in ('B1','B2','B3','B4','B5') and int(b['tier'][1])<=maxb
                and json.loads(b['metadata_json'] or '{}').get('hb_id')]
        brands=[b for b in brands if db.execute("SELECT 1 FROM rp_history_assets_v23 WHERE guild_id=? AND pool='general' AND asset_id=? AND tier=? AND approved=1 AND enabled=1",(gid,json.loads(b['metadata_json'])['hb_id'],b['tier'])).fetchone()]
        needs_cname=('演員' in occupation or '配音' in occupation)
        names=pool('CN') if needs_cname else []
        if not works or not brands or len(events)<3 or (needs_cname and not names):
            reason=f'尚缺少可抽素材：作品={len(works)}、代言={len(brands)}、事件={len(events)}、劇中姓名={len(names)}'
            db.execute('INSERT INTO rp_history_pending_v23 VALUES (?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id,character_id) DO UPDATE SET reason=excluded.reason,updated_at=CURRENT_TIMESTAMP',(gid,cid,reason))
            return 'pending_assets'
        # 單一交易內全數選中、全數儲存，遇失敗即回滾。
        rng=random.SystemRandom()
        def rarity_draw(rows):
            target=rng.choices(RARITIES,weights=weights,k=1)[0]
            ix=RARITIES.index(target)
            eligible=[r for r in rows if r['rarity']==target]
            while not eligible and ix>0:
                ix-=1;eligible=[r for r in rows if r['rarity']==RARITIES[ix]]
            return rng.choice(eligible) if eligible else None
        # WN 名稱不是固定 HE 稀有度；若未指定稀有度，作品參與時隨機決定。
        first=rarity_draw(works)
        if first is None and works and all(not r['rarity'] for r in works):
            first=dict(rng.choice(works))
            first['rarity']=rng.choices(RARITIES,weights=weights,k=1)[0]
        second=rarity_draw(brands)
        if first is None or second is None:
            return 'pending_assets'
        drawn=[];remaining=events[:]
        for _ in range(3):
            item=rarity_draw(remaining)
            if not item:return 'pending_assets'
            drawn.append(item);remaining.remove(item)
        # 作品同名同類共用身分；明確唯一席位一人一位，不能互相覆蓋。
        work_type=json.loads(first['metadata_json'] or '{}').get('work_type','作品')
        normalized=re.sub(r'\s+','',first['title']).casefold()
        db.execute('INSERT OR IGNORE INTO rp_history_works_v23(guild_id,work_type,normalized_title,title) VALUES (?,?,?,?)',
            (gid,work_type,normalized,first['title']))
        work_id=db.execute('SELECT id FROM rp_history_works_v23 WHERE guild_id=? AND work_type=? AND normalized_title=?',
            (gid,work_type,normalized)).fetchone()['id']
        role_options=json.loads(first['metadata_json'] or '{}').get('roles',[])
        if not role_options:role_options=[occupation or '參與者']
        rng.shuffle(role_options)
        seat=None
        for role in role_options:
            occupied=db.execute('SELECT 1 FROM rp_history_participants_v23 WHERE guild_id=? AND work_id=? AND seat=?',
                (gid,work_id,role)).fetchone()
            if not occupied:seat=role;break
        if seat is None:
            db.execute('INSERT INTO rp_history_pending_v23 VALUES (?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(guild_id,character_id) DO UPDATE SET reason=excluded.reason,updated_at=CURRENT_TIMESTAMP',
                (gid,cid,'代表作品所有合法席位均已被占用'))
            return 'pending_seat'
        character_name=rng.choice(names)['title'] if needs_cname else ''
        db.execute('INSERT INTO rp_history_participants_v23(guild_id,work_id,character_id,seat,role_name) VALUES (?,?,?,?,?)',
            (gid,work_id,cid,seat,character_name))
        first=dict(first);first['work_id']=work_id;first['participation_role']=seat;first['character_name']=character_name
        for slot,(kind,item) in enumerate([('work',first),('endorsement',second)]+[('event',x) for x in drawn],1):
            db.execute('INSERT INTO rp_history_results_v23(guild_id,character_id,slot,kind,asset_id,snapshot_json) VALUES (?,?,?,?,?,?)',
                (gid,cid,slot,kind,item['asset_id'],json.dumps(item,ensure_ascii=False)))
        db.execute('DELETE FROM rp_history_pending_v23 WHERE guild_id=? AND character_id=?',(gid,cid))
        return 'locked'


def put_batch(gid, rows, *, pool='general'):
    """群主核准後批次匯入：所有項目先驗證，整批同交易提交。"""
    if pool not in POOLS:raise ValueError('未知素材池。')
    if not isinstance(rows,list) or not 1<=len(rows)<=250:raise ValueError('每批須為1～250筆 JSON 陣列。')
    prepared=[];keys=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('每筆素材必須是 JSON 物件。')
        key=str(row.get('asset_id','')).upper().strip()
        title=str(row.get('title','')).strip()
        tier=str(row.get('tier','')).strip()
        rarity=str(row.get('rarity','')).strip()
        metadata=row.get('meta',{})
        if not re.fullmatch(r'(WN|CN|HB|HE)-\d{4}',key) or not title or key in keys:
            raise ValueError(f'無效或重複素材編號：{key}')
        if rarity and rarity not in RARITIES:raise ValueError(f'{key} 稀有度錯誤')
        if not isinstance(metadata,dict):raise ValueError(f'{key} meta 必須為 JSON 物件')
        if key.startswith('HE-') and 151<=int(key.split('-')[1])<=230:
            if not metadata.get('hb_id') or not tier.startswith('B'):
                raise ValueError(f'{key} 必須指定已核准 HB 編號與 B 級')
        keys.add(key);prepared.append((key,title,tier,rarity,metadata))
    with transaction() as db:
        init(db)
        for key,title,tier,rarity,meta in prepared:
            existing=db.execute('SELECT title,tier,rarity,metadata_json FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=?',
                (str(gid),pool,key)).fetchone()
            if existing:
                if existing['title']!=title or existing['tier']!=tier or existing['rarity']!=rarity:
                    raise ValueError(f'{key} 已存在不同的已核准內容，整批取消。')
                continue
            db.execute('''INSERT INTO rp_history_assets_v23
                (guild_id,pool,asset_id,category,title,tier,rarity,metadata_json,approved,enabled)
                VALUES (?,?,?,?,?,?,?,?,1,1)''',(str(gid),pool,key,key.split('-',1)[0],title,tier,rarity,json.dumps(meta,ensure_ascii=False)))
    return len(prepared)
