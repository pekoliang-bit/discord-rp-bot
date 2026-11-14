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
    if tier and not re.fullmatch(r'B[1-5]',tier):raise ValueError('品牌等級只接受 B1～B5。')
    if category=='HE' and 151 <= int(asset_id.split('-')[1]) <= 230:
        if not isinstance(meta,dict) or not re.fullmatch(r'HB-\d{4}',str(meta.get('hb_id',''))):
            raise ValueError('歷史代言必須指定已核准 HB 編號。')
    with transaction() as db:
        init(db)
        if category=='HE' and 151 <= int(asset_id.split('-')[1]) <= 230:
            linked=db.execute('SELECT tier FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=? AND approved=1 AND enabled=1',
                (str(gid),pool,meta['hb_id'])).fetchone()
            if not linked or linked['tier']!=tier:
                raise ValueError('代言 HB 品牌未核准、被停用或等級不一致。')
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


def attempt_lock(gid, cid):
    """正式 active 後一次鎖定五筆；絕不在送審時預抽、絕不發獎。

    保底 WN 代表作品一筆與 HE 歷史代言一筆；其餘三筆依
    作品30%、代言25%、成就25%、榮譽20% 抽取。從未核准、停用、
    缺少類別標記的資料不得進入候選池。事件的成就／榮譽分類
    必須出自已核准 meta.history_group，不自行推論或覆寫原文。
    """
    gid, cid = str(gid), int(cid)
    rng = random.SystemRandom()
    with transaction() as db:
        init(db)
        character = db.execute('''SELECT character_type,occupation,status,settings_json FROM characters
            WHERE guild_id=? AND id=?''', (gid,cid)).fetchone()
        if not character or character['status'] != 'active' or character['character_type'] not in ('player','player_npc','official_npc'):
            return 'not_active'
        existing = db.execute('''SELECT COUNT(*) FROM rp_history_results_v23
            WHERE guild_id=? AND character_id=?''',(gid,cid)).fetchone()[0]
        if existing == 5:
            return 'already'
        if existing:
            raise ValueError('已有不足五筆的歷史履歷；必須人工修復，不能重新抽取。')
        occupation = str(character['occupation'] or '')
        try:
            settings = json.loads(character['settings_json'] or '{}')
            if not isinstance(settings,dict):settings = {}
        except (ValueError,TypeError):settings = {}
        years = settings.get('experience_years', settings.get('career_years',settings.get('出道資歷',0)))
        try: years = max(0,int(years))
        except (TypeError,ValueError):years = 0
        weights = WEIGHTS[0 if years<=1 else 1 if years<=4 else 2 if years<=9 else 3]
        assets = [dict(x) for x in db.execute('''SELECT * FROM rp_history_assets_v23
            WHERE guild_id=? AND pool='general' AND approved=1 AND enabled=1''',(gid,))]
        def metadata(item):
            try:return json.loads(item['metadata_json'] or '{}')
            except (TypeError,ValueError):return {}
        def allowed_work(item):
            m=metadata(item)
            jobs=m.get('occupations') or []
            return not jobs or occupation in jobs
        works=[x for x in assets if x['category']=='WN' and allowed_work(x)]
        names=[x for x in assets if x['category']=='CN']
        max_tier=3 if character['character_type']=='player' else 5
        max_title=4 if character['character_type']=='player' else 6
        hb={x['asset_id']:x for x in assets if x['category']=='HB'}
        endorses=[]
        for x in assets:
            if x['category']!='HE' or not 'HE-0151'<=x['asset_id']<='HE-0230':continue
            m=metadata(x)
            linked=hb.get(m.get('hb_id'))
            if not linked or linked['tier']!=x['tier'] or x['tier'] not in ('B1','B2','B3','B4','B5'):
                continue
            if int(x['tier'][1])>max_tier:continue
            title_tier=m.get('title_tier')
            if title_tier is not None:
                try:
                    if int(title_tier)>max_title:continue
                except (ValueError,TypeError):continue
            endorses.append(x)
        achievements=[];honors=[];viral=[]
        for x in assets:
            if x['category']!='HE' or not 'HE-0001'<=x['asset_id']<='HE-0150':continue
            m=metadata(x)
            if m.get('history_group')=='achievement':achievements.append(x)
            elif m.get('history_group')=='honor':honors.append(x)
            if m.get('hot_search') is True:viral.append(x)
        influencer='網紅' in occupation or '網紅' in str(settings.get('main_track',''))
        if influencer:
            work_candidates=[x for x in works if metadata(x).get('work_type') in ('網路作品','直播','網路直播','短影音')]
        else:work_candidates=works[:]
        needs_name=('演員' in occupation or '配音' in occupation)
        pools={'work':works,'endorsement':endorses,'achievement':achievements,'honor':honors}
        # 每一類必須有足夠不同素材，才能在三次混抽中不重複抽到同 ID。
        missing=[k for k,v in pools.items() if len(v)<(4 if k in ('work','endorsement') else 3)]
        if not work_candidates or (needs_name and not names) or (influencer and not viral) or missing:
            reason='素材尚未齊備：'+', '.join(f'{k}={len(v)}' for k,v in pools.items())
            if influencer and not viral:reason+='；網紅熱搜素材缺失'
            if needs_name and not names:reason+='；劇中姓名缺失'
            db.execute('''INSERT INTO rp_history_pending_v23(guild_id,character_id,reason)
                VALUES (?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
                reason=excluded.reason,updated_at=CURRENT_TIMESTAMP''',(gid,cid,reason))
            return 'pending_assets'
        def choose(rows, used):
            options=[x for x in rows if x['asset_id'] not in used]
            if not options:return None
            wanted=rng.choices(RARITIES,weights=weights,k=1)[0]
            tiers=[r for r in options if r['rarity']==wanted]
            if not tiers:
                ix=RARITIES.index(wanted)
                for j in list(range(ix-1,-1,-1))+list(range(ix+1,5)):
                    tiers=[r for r in options if r['rarity']==RARITIES[j]]
                    if tiers:break
            if not tiers:tiers=options  # WN 通常不預設稀有度，按資歷抽工作等級
            result=dict(rng.choice(tiers))
            if not result['rarity']:
                result['rarity']=wanted
            return result
        def available_seat(item):
            m=metadata(item)
            wt=m.get('work_type','作品')
            normalized=re.sub(r'\\s+','',item['title']).casefold()
            work=db.execute('SELECT id FROM rp_history_works_v23 WHERE guild_id=? AND work_type=? AND normalized_title=?',
                (gid,wt,normalized)).fetchone()
            seats=m.get('roles') or []
            if not seats: # 非排他性群演／通告／演唱等參與可多人重複
                return (wt,normalized,occupation or '參與者',f'{occupation or "參與"}:CHAR:{cid}',work)
            seats=list(dict.fromkeys(str(r) for r in seats if str(r).strip()))
            rng.shuffle(seats)
            for seat in seats:
                if not work or not db.execute('''SELECT 1 FROM rp_history_participants_v23
                    WHERE guild_id=? AND work_id=? AND seat=?''',(gid,work['id'],seat)).fetchone():
                    return (wt,normalized,seat,seat,work)
            return None
        # 優先選尚有合法席位的代表作品；不能悄悄擠掉別人的角色。
        candidates=work_candidates[:];rng.shuffle(candidates)
        selected=None;seat_data=None
        for item in candidates:
            result=available_seat(item)
            if result:
                selected=dict(item);seat_data=result;break
        if not selected:
            db.execute('''INSERT INTO rp_history_pending_v23(guild_id,character_id,reason)
                VALUES (?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET
                reason=excluded.reason,updated_at=CURRENT_TIMESTAMP''',(gid,cid,'代表作品沒有合法剩餘席位'))
            return 'pending_seat'
        used={selected['asset_id']}
        selected['rarity']=rng.choices(RARITIES,weights=weights,k=1)[0]
        second=choose(endorses,used)
        if not second:return 'pending_assets'
        used.add(second['asset_id'])
        kinds=['work','endorsement','achievement','honor']
        drawn=[]
        for _ in range(3):
            available_kinds=[k for k in kinds if any(x['asset_id'] not in used and (k!='work' or available_seat(x)) for x in pools[k])]
            if len(available_kinds)!=4:return 'pending_assets'
            category=rng.choices(kinds,weights=(30,25,25,20),k=1)[0]
            choice=choose([x for x in pools[category] if category!='work' or available_seat(x)],used)
            if not choice:return 'pending_assets'
            used.add(choice['asset_id']);drawn.append((category,choice))
        # 五筆中的每一部作品（不只代表作品）都共用同一作品 ID 與席位表。
        # 所有寫入仍位於單一 transaction，若任何席位衝突則整批回滾。
        outputs=[('work',selected),('endorsement',second)]+drawn
        for kind,item in outputs:
            if kind!='work':continue
            if item is selected:current=seat_data
            else:current=available_seat(item)
            if not current:raise ValueError('抽取過程作品席位已滿，請管理員檢查。')
            wt,normalized,role,seat,_work=current
            db.execute('''INSERT OR IGNORE INTO rp_history_works_v23
                (guild_id,work_type,normalized_title,title) VALUES (?,?,?,?)''',
                (gid,wt,normalized,item['title']))
            work_id=db.execute('''SELECT id FROM rp_history_works_v23 WHERE
                guild_id=? AND work_type=? AND normalized_title=?''',(gid,wt,normalized)).fetchone()['id']
            role_name=rng.choice(names)['title'] if needs_name else ''
            db.execute('''INSERT INTO rp_history_participants_v23
                (guild_id,work_id,character_id,seat,role_name) VALUES (?,?,?,?,?)''',
                (gid,work_id,cid,seat,role_name))
            item['work_id']=work_id;item['participation_role']=role;item['character_name']=role_name
        if influencer:
            hot=rng.choice(viral)
            selected['paired_hot_search']={'asset_id':hot['asset_id'],'title':hot['title']}
        for slot,(kind,item) in enumerate(outputs,1):
            db.execute('''INSERT INTO rp_history_results_v23
                (guild_id,character_id,slot,kind,asset_id,snapshot_json)
                VALUES (?,?,?,?,?,?)''',(gid,cid,slot,kind,item['asset_id'],json.dumps(item,ensure_ascii=False)))
        db.execute('DELETE FROM rp_history_pending_v23 WHERE guild_id=? AND character_id=?',(gid,cid))
        return 'locked'


def put_batch(gid, rows, *, pool='general'):
    """群主核准後批次匯入：所有項目先驗證，整批同交易提交。"""
    if pool not in POOLS:raise ValueError('未知素材池。')
    if not isinstance(rows,list) or not 1<=len(rows)<=250:raise ValueError('每批須為1～250筆 JSON 陣列。')
    prepared=[];keys=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('每筆素材必須是 JSON 物件。')
        if (row.get('import_ready') is False or row.get('approved') is False
            or row.get('review_status') or row.get('status') in
            ('source_text_missing','recovered_summary_needs_original_text_review')):
            raise ValueError('不得將未確認的素材索引／候選資料直接當成核准素材。')
        key=str(row.get('asset_id','')).upper().strip()
        title=str(row.get('title','')).strip()
        tier=str(row.get('tier','')).strip()
        rarity=str(row.get('rarity','')).strip()
        metadata=row.get('meta',{})
        if not re.fullmatch(r'(WN|CN|HB|HE)-\d{4}',key) or not title or key in keys:
            raise ValueError(f'無效或重複素材編號：{key}')
        if rarity and rarity not in RARITIES:raise ValueError(f'{key} 稀有度錯誤')
        if tier and not re.fullmatch(r'B[1-5]',tier):raise ValueError(f'{key} 的 B 等級格式無效')
        if not isinstance(metadata,dict):raise ValueError(f'{key} meta 必須為 JSON 物件')
        if key.startswith('HE-') and 151<=int(key.split('-')[1])<=230:
            if not metadata.get('hb_id') or not tier.startswith('B'):
                raise ValueError(f'{key} 必須指定已核准 HB 編號與 B 級')
        keys.add(key);prepared.append((key,title,tier,rarity,metadata))
    with transaction() as db:
        init(db)
        # 跨素材驗證：必須是同一批次或資料庫內已核准且啟用的 HB，等級不得不符。
        in_batch={key:(tier,meta) for key,_title,tier,_rarity,meta in prepared}
        for key,title,tier,rarity,meta in prepared:
            if key.startswith('HE-') and 151<=int(key.split('-')[1])<=230:
                brand=meta['hb_id']
                if brand in in_batch:
                    if in_batch[brand][0]!=tier:raise ValueError(f'{key} 對應的 {brand} 等級不一致')
                else:
                    linked=db.execute('SELECT tier FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=? AND approved=1 AND enabled=1',
                                      (str(gid),pool,brand)).fetchone()
                    if not linked or linked['tier']!=tier:
                        raise ValueError(f'{key} 的 HB 品牌不存在、被停用或等級不一致，整批取消。')
        for key,title,tier,rarity,meta in prepared:
            existing=db.execute('SELECT title,tier,rarity,metadata_json FROM rp_history_assets_v23 WHERE guild_id=? AND pool=? AND asset_id=?',
                (str(gid),pool,key)).fetchone()
            if existing:
                if (existing['title']!=title or existing['tier']!=tier or existing['rarity']!=rarity
                    or json.loads(existing['metadata_json'] or '{}')!=meta):
                    raise ValueError(f'{key} 已存在不同的已核准內容，整批取消。')
                continue
            db.execute('''INSERT INTO rp_history_assets_v23
                (guild_id,pool,asset_id,category,title,tier,rarity,metadata_json,approved,enabled)
                VALUES (?,?,?,?,?,?,?,?,1,1)''',(str(gid),pool,key,key.split('-',1)[0],title,tier,rarity,json.dumps(meta,ensure_ascii=False)))
    return len(prepared)
