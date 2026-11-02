
"""路人批次創建資料庫：每服隔離、照片僅一次使用、批次建立原子交易。"""
from __future__ import annotations
import json, random, re
from core.database import transaction, get_connection

DEFAULT_JOBS = ['演員','舞者','歌手','導演','模特兒','編劇','主持人','配音員','編舞師','製作人']
DEFAULT_LEVELS = {1:'無名素人'}

def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS extra_photos (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, url TEXT NOT NULL, used_character_id INTEGER, UNIQUE(guild_id,url))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_name_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, nationality TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(guild_id,nationality,kind,value))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_jobs (guild_id TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(guild_id,job))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_settings (guild_id TEXT PRIMARY KEY, allow_ranking INTEGER NOT NULL DEFAULT 1, min_age INTEGER NOT NULL DEFAULT 18, max_age INTEGER NOT NULL DEFAULT 65, min_level INTEGER NOT NULL DEFAULT 1, max_level INTEGER NOT NULL DEFAULT 20)""")

def add_photos(gid, urls):
    initialize(); count=0
    with transaction() as db:
        for url in dict.fromkeys(urls):
            url=url.strip()
            if not url.startswith(('https://','http://')) or len(url)>1800: continue
            cur=db.execute('INSERT OR IGNORE INTO extra_photos(guild_id,url) VALUES(?,?)',(str(gid),url))
            count += cur.rowcount
    return count

def add_names(gid, nationality, kind, values):
    if kind not in ('surname','given'): raise ValueError('名字類型必須是 surname 或 given')
    initialize(); count=0
    with transaction() as db:
        for name in dict.fromkeys(values):
            name=name.strip()
            if not name or len(name)>80: continue
            count+=db.execute('INSERT OR IGNORE INTO extra_name_pool(guild_id,nationality,kind,value) VALUES(?,?,?,?)',(str(gid),nationality,kind,name)).rowcount
    return count

def add_jobs(gid, jobs):
    initialize(); count=0
    with transaction() as db:
        for job in dict.fromkeys(jobs):
            job=job.strip()
            if job and len(job)<=80: count+=db.execute('INSERT OR IGNORE INTO extra_jobs VALUES(?,?)',(str(gid),job)).rowcount
    return count

def inventory(gid):
    initialize()
    with get_connection() as db:
        photos=db.execute('SELECT COUNT(*) FROM extra_photos WHERE guild_id=? AND used_character_id IS NULL',(str(gid),)).fetchone()[0]
        nations=db.execute("SELECT COUNT(DISTINCT nationality) FROM extra_name_pool WHERE guild_id=?",(str(gid),)).fetchone()[0]
        names=db.execute('SELECT COUNT(*) FROM extra_name_pool WHERE guild_id=?',(str(gid),)).fetchone()[0]
        jobs=db.execute('SELECT COUNT(*) FROM extra_jobs WHERE guild_id=?',(str(gid),)).fetchone()[0]
        existing=db.execute("SELECT COUNT(*) FROM characters WHERE guild_id=? AND json_extract(settings_json,'$.character_category')='extra'",(str(gid),)).fetchone()[0]
    return dict(photos=photos,nations=nations,names=names,jobs=jobs,existing=existing)

def get_config(gid):
    initialize()
    with get_connection() as db:
        row=db.execute('SELECT * FROM extra_settings WHERE guild_id=?',(str(gid),)).fetchone()
    return dict(row) if row else dict(allow_ranking=1,min_age=18,max_age=65,min_level=1,max_level=20)

def set_ranking(gid,enabled):
    initialize()
    with transaction() as db:
        db.execute('INSERT INTO extra_settings(guild_id,allow_ranking) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET allow_ranking=excluded.allow_ranking',(str(gid),int(enabled)))

def _world_levels(gid):
    try:
        from modules import content_builder
        values=content_builder.world_get(gid).get('status_levels',{})
        return values if isinstance(values,dict) else {}
    except (ImportError,AttributeError,KeyError): return {}

def _title(level, levels):
    item=levels.get(str(level), levels.get(level,{}))
    if isinstance(item,dict): return str(item.get('name') or item.get('label') or DEFAULT_LEVELS.get(level,'未命名地位'))
    if isinstance(item,str): return item
    return DEFAULT_LEVELS.get(level,'未命名地位')

def generate(gid,owner_id,count,*,nationality=None,level=None):
    initialize()
    count=int(count)
    if not 1<=count<=100: raise ValueError('一次只能建立 1～100 位路人')
    cfg=get_config(gid)
    levels=_world_levels(gid)
    created=[]
    with transaction() as db:
        photos=db.execute('SELECT id,url FROM extra_photos WHERE guild_id=? AND used_character_id IS NULL ORDER BY id LIMIT ?',(str(gid),count)).fetchall()
        if len(photos)<count: raise ValueError(f'照片不足：目前只剩 {len(photos)} 張可用，無法建立 {count} 人。')
        rows=db.execute('SELECT nationality,kind,value FROM extra_name_pool WHERE guild_id=?',(str(gid),)).fetchall()
        pool={}
        for r in rows: pool.setdefault(r['nationality'],{}).setdefault(r['kind'],[]).append(r['value'])
        valid=[n for n,parts in pool.items() if parts.get('surname') and parts.get('given')]
        if nationality:
            if nationality not in valid: raise ValueError(f'國籍 {nationality} 的姓氏或名字庫不足。')
            valid=[nationality]
        if not valid: raise ValueError('先為至少一個國籍加入姓氏及名字庫。')
        jobs=[r[0] for r in db.execute('SELECT job FROM extra_jobs WHERE guild_id=?',(str(gid),)).fetchall()]
        if not jobs: raise ValueError('請先建立職業庫。')
        lo,hi=int(cfg['min_level']),int(cfg['max_level'])
        if level is not None and not lo<=int(level)<=hi: raise ValueError('指定咖位超出允許範圍。')
        used_names={r[0] for r in db.execute('SELECT name FROM characters WHERE guild_id=?',(str(gid),)).fetchall()}
        for photo in photos:
            nation=random.choice(valid); parts=pool[nation]
            name=None
            for _ in range(300):
                candidate=random.choice(parts['surname'])+' '+random.choice(parts['given'])
                if candidate not in used_names: name=candidate;break
            if name is None: raise ValueError(f'{nation} 的可用姓名組合不足，無法繼續生成不重名角色。')
            used_names.add(name)
            lv=int(level) if level is not None else random.randint(lo,hi)
            age=random.randint(int(cfg['min_age']),int(cfg['max_age']))
            occupation=random.choice(jobs)
            settings={'character_category':'extra','public_category':'extra','created_as_extra':True,'portrait_url':photo['url'],'nationality':nation,'status_level':lv,'level':lv,'status_title':_title(lv,levels),'rank_display':f'LV{lv} {_title(lv,levels)}','ranking_eligible':True,'biography':'','public_biography':''}
            cur=db.execute("INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type,settings_json) VALUES(?,?,?,?,?,'active','official_npc',?)",(str(gid),str(owner_id),name,age,occupation,json.dumps(settings,ensure_ascii=False)))
            cid=cur.lastrowid
            db.execute('UPDATE extra_photos SET used_character_id=? WHERE id=? AND used_character_id IS NULL',(cid,photo['id']))
            created.append((cid,name,occupation,lv,_title(lv,levels)))
    return created
