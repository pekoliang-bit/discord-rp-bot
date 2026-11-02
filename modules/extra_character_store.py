
"""路人批次創建資料庫：每服隔離、照片僅一次使用、批次建立原子交易。"""
from __future__ import annotations
import json, random, re
from core.database import transaction, get_connection

DEFAULT_JOBS = ['演員','舞者','歌手','導演','模特兒','編劇','主持人','配音員','編舞師','製作人']
DEFAULT_LEVELS = {1:'無名素人'}

def initialize(gid=None):
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS extra_photos (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, url TEXT NOT NULL, used_character_id INTEGER, UNIQUE(guild_id,url))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_name_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, nationality TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(guild_id,nationality,kind,value))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_jobs (guild_id TEXT NOT NULL, job TEXT NOT NULL, PRIMARY KEY(guild_id,job))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_photo_meta (photo_id INTEGER PRIMARY KEY, gender TEXT NOT NULL, age_group TEXT NOT NULL, source_channel_id TEXT, source_message_id TEXT, UNIQUE(source_channel_id,source_message_id,photo_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_photo_channels (guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, gender TEXT NOT NULL, age_group TEXT NOT NULL, PRIMARY KEY(guild_id,channel_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS extra_settings (guild_id TEXT PRIMARY KEY, allow_ranking INTEGER NOT NULL DEFAULT 1, min_age INTEGER NOT NULL DEFAULT 18, max_age INTEGER NOT NULL DEFAULT 65, min_level INTEGER NOT NULL DEFAULT 1, max_level INTEGER NOT NULL DEFAULT 20)""")
        db.execute("CREATE TABLE IF NOT EXISTS extra_seeded_guilds (guild_id TEXT PRIMARY KEY)")
        if gid is not None and not db.execute('SELECT 1 FROM extra_seeded_guilds WHERE guild_id=?',(str(gid),)).fetchone():
            from modules.extra_character_seed import seed_into_connection
            seed_into_connection(db,gid)
            db.execute('INSERT OR IGNORE INTO extra_seeded_guilds(guild_id) VALUES(?)',(str(gid),))

def add_photos(gid, urls, *, gender="male", age_group="adult", channel_id=None, message_id=None):
    if gender not in ("male", "female") or age_group not in ("adult", "minor"):
        raise ValueError("照片必須指定男／女與成年／未成年分類")
    initialize(gid); count=0
    with transaction() as db:
        for url in dict.fromkeys(urls):
            url=url.strip()
            if not url.startswith(('https://','http://')) or len(url)>1800: continue
            cur=db.execute('INSERT OR IGNORE INTO extra_photos(guild_id,url) VALUES(?,?)',(str(gid),url))
            photo=db.execute('SELECT id FROM extra_photos WHERE guild_id=? AND url=?',(str(gid),url)).fetchone()
            if photo and (cur.rowcount or not db.execute('SELECT 1 FROM extra_photo_meta WHERE photo_id=?',(photo[0],)).fetchone()):
                db.execute('INSERT OR IGNORE INTO extra_photo_meta(photo_id,gender,age_group,source_channel_id,source_message_id) VALUES(?,?,?,?,?)',(photo[0],gender,age_group,str(channel_id) if channel_id else None,str(message_id) if message_id else None))
            count += cur.rowcount
    return count

def add_names(gid, nationality, kind, values):
    if kind not in ('surname','given'): raise ValueError('名字類型必須是 surname 或 given')
    initialize(gid); count=0
    with transaction() as db:
        for name in dict.fromkeys(values):
            name=name.strip()
            if not name or len(name)>80: continue
            count+=db.execute('INSERT OR IGNORE INTO extra_name_pool(guild_id,nationality,kind,value) VALUES(?,?,?,?)',(str(gid),nationality,kind,name)).rowcount
    return count

def add_jobs(gid, jobs):
    initialize(gid); count=0
    with transaction() as db:
        for job in dict.fromkeys(jobs):
            job=job.strip()
            if job and len(job)<=80: count+=db.execute('INSERT OR IGNORE INTO extra_jobs VALUES(?,?)',(str(gid),job)).rowcount
    return count

def set_photo_channel(gid, channel_id, gender, age_group):
    if gender not in ('male','female') or age_group not in ('adult','minor'): raise ValueError('照片分類不正確')
    initialize(gid)
    with transaction() as db:
        db.execute('INSERT OR REPLACE INTO extra_photo_channels VALUES(?,?,?,?)',(str(gid),str(channel_id),gender,age_group))

def get_photo_channel(gid, channel_id):
    initialize(gid)
    with get_connection() as db:
        row=db.execute('SELECT gender,age_group FROM extra_photo_channels WHERE guild_id=? AND channel_id=?',(str(gid),str(channel_id))).fetchone()
        return dict(row) if row else None

def photo_inventory(gid):
    initialize(gid)
    with get_connection() as db:
        rows=db.execute('SELECT m.gender,m.age_group,COUNT(*) AS remaining FROM extra_photos p JOIN extra_photo_meta m ON p.id=m.photo_id WHERE p.guild_id=? AND p.used_character_id IS NULL GROUP BY m.gender,m.age_group',(str(gid),)).fetchall()
    return {(r['gender'],r['age_group']):r['remaining'] for r in rows}

def inventory(gid):
    initialize(gid)
    with get_connection() as db:
        photos=db.execute('SELECT COUNT(*) FROM extra_photos WHERE guild_id=? AND used_character_id IS NULL',(str(gid),)).fetchone()[0]
        nations=db.execute("SELECT COUNT(DISTINCT nationality) FROM extra_name_pool WHERE guild_id=?",(str(gid),)).fetchone()[0]
        names=db.execute('SELECT COUNT(*) FROM extra_name_pool WHERE guild_id=?',(str(gid),)).fetchone()[0]
        jobs=db.execute('SELECT COUNT(*) FROM extra_jobs WHERE guild_id=?',(str(gid),)).fetchone()[0]
        existing=db.execute("SELECT COUNT(*) FROM characters WHERE guild_id=? AND json_extract(settings_json,'$.character_category')='extra'",(str(gid),)).fetchone()[0]
    return dict(photos=photos,nations=nations,names=names,jobs=jobs,existing=existing)

def get_config(gid):
    initialize(gid)
    with get_connection() as db:
        row=db.execute('SELECT * FROM extra_settings WHERE guild_id=?',(str(gid),)).fetchone()
    return dict(row) if row else dict(allow_ranking=1,min_age=18,max_age=65,min_level=1,max_level=20)

def set_ranking(gid,enabled):
    initialize(gid)
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

def generate(gid,owner_id,count,*,nationality=None,level=None,gender=None,age_group=None):
    from modules.extra_character_profile import generate_profile, write_profile
    initialize(gid)
    count=int(count)
    if gender not in (None,'male','female') or age_group not in (None,'adult','minor'): raise ValueError('性別與年齡分類不正確')
    if not 1<=count<=100: raise ValueError('一次只能建立 1～100 位路人')
    cfg=get_config(gid)
    levels=_world_levels(gid)
    created=[]
    with transaction() as db:
        sql="SELECT p.id,p.url,m.gender,m.age_group FROM extra_photos p JOIN extra_photo_meta m ON m.photo_id=p.id WHERE p.guild_id=? AND p.used_character_id IS NULL"
        params=[str(gid)]
        if gender: sql+=' AND m.gender=?';params.append(gender)
        if age_group: sql+=' AND m.age_group=?';params.append(age_group)
        sql+=' ORDER BY RANDOM() LIMIT ?';params.append(count)
        photos=db.execute(sql,params).fetchall()
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
            age=random.randint(12,17) if photo['age_group']=='minor' else random.randint(max(18,int(cfg['min_age'])),max(18,int(cfg['min_age']),int(cfg['max_age'])))
            occupation=random.choice(jobs)
            profile=generate_profile(age,occupation,lv,photo['gender'])
            settings={'character_category':'extra','public_category':'extra','created_as_extra':True,'portrait_url':photo['url'],'nationality':nation,'status_level':lv,'level':lv,'status_title':_title(lv,levels),'rank_display':f'LV{lv} {_title(lv,levels)}','ranking_eligible':True,**profile,'biography':'','public_biography':''}
            cur=db.execute("INSERT INTO characters(guild_id,owner_id,name,age,occupation,status,character_type,settings_json) VALUES(?,?,?,?,?,'active','official_npc',?)",(str(gid),str(owner_id),name,age,occupation,json.dumps(settings,ensure_ascii=False)))
            cid=cur.lastrowid
            write_profile(db,str(gid),cid,profile)
            db.execute('UPDATE extra_photos SET used_character_id=? WHERE id=? AND used_character_id IS NULL',(cid,photo['id']))
            created.append((cid,name,occupation,lv,_title(lv,levels)))
    return created
