"""九檔綜藝初始資料、空缺席位及按期通告。只補缺，不覆蓋官方修改。

既定出演人員只有姓名，未有 character_id 映射時不偽造 Discord 角色。
沒有官方錄製排程前，任務不假裝已佔用拍攝檔期。
"""
from __future__ import annotations

import json
from core.database import transaction, get_connection
from modules.task_repository import initialize

# key, 名稱, 類型, 建議級別, 主技能, 主持上限, 常駐上限, 飛行上限, 總期數, 每期遊戲天, 已占主持, 已占常駐, 已占飛行
SHOWS = (
 ('night','夜行劇團','競技綜藝',2,'綜藝',0,3,3,8,2,(),(),()),
 ('survival','極限逃生指南','真人秀',3,'綜藝',0,5,0,6,3,(),('謝賦青','植田花繪','夏芷','江硯'),()),
 ('street','街角放映室','生活綜藝',2,'綜藝',0,4,0,6,3,(),('斯特林','謝賦青','芙蓉','植田花繪'),()),
 ('partners','危險的合夥人','真人秀',3,'綜藝',0,4,0,6,2,(),('謝賦青','植田花繪','夏芷','江硯'),()),
 ('cook','今晚誰掌廚','生活綜藝',1,'綜藝',2,0,4,8,1,(),(),()),
 ('escape','零點密室','競技綜藝',2,'綜藝',0,4,2,8,2,(),(),()),
 ('travel','出發吧，沒有目的地！','真人秀',1,'綜藝',0,3,3,6,4,(),(),()),
 ('music','聲音交換計畫','音樂綜藝',3,'歌唱',2,0,6,8,2,(),(),()),
 ('midnight','凌晨兩點營業中','訪談',2,'綜藝',2,0,2,10,1,(),(),()),
)
# 已知參與者但席位類型未確定，保留待確認資料，不擅自扣常駐或飛行名額。
UNASSIGNED = {'night': ('植田花繪',)}
MAIN_THRESHOLDS = {'host':(300,600,1500,3000,6000), 'resident':(200,400,800,1500,3500), 'flight':(120,300,600,1500,2500)}
CAFE_THRESHOLDS = {'host':(2,5,8,12,16), 'resident':(1,3,6,10,13), 'flight':(1,1,3,6,9)}
PAY = {'host':(12000,36000,108000,324000,972000), 'resident':(8000,24000,72000,216000,648000), 'flight':(6000,18000,54000,162000,486000)}
SKILL_REWARD = {'host':(12,25,50,100,200), 'resident':(10,20,40,80,160), 'flight':(8,15,30,60,120)}
EXPOSURE = {1:(500,300,100),2:(1500,900,300),3:(4500,2700,900),4:(13500,8100,2700),5:(40500,24300,8100)}
JOBS = ('演員','歌手','偶像','主持人','網紅','模特兒','舞者')
SHOW_BY_KEY={x[0]:x for x in SHOWS}


def initialize_variety():
    initialize()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_shows (
            guild_id TEXT NOT NULL, show_key TEXT NOT NULL, title TEXT NOT NULL,
            subtype TEXT NOT NULL, level INTEGER NOT NULL, main_skill TEXT NOT NULL,
            total_episodes INTEGER NOT NULL, game_days_per_episode REAL NOT NULL,
            PRIMARY KEY(guild_id,show_key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_seats (
            guild_id TEXT NOT NULL,show_key TEXT NOT NULL,seat TEXT NOT NULL,
            capacity INTEGER NOT NULL,reserved_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(guild_id,show_key,seat))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_fixed_cast (
            guild_id TEXT NOT NULL,show_key TEXT NOT NULL,artist TEXT NOT NULL,
            seat TEXT NOT NULL,PRIMARY KEY(guild_id,show_key,artist))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_cast_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,show_key TEXT NOT NULL,
            character_id INTEGER NOT NULL,seat TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',reviewer_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,reviewed_at TEXT,
            UNIQUE(guild_id,show_key,character_id,seat),
            CHECK(status IN ('pending','approved','rejected')) )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_cast_roster (
            guild_id TEXT NOT NULL,show_key TEXT NOT NULL,character_id INTEGER NOT NULL,
            seat TEXT NOT NULL,approved_by TEXT NOT NULL,
            PRIMARY KEY(guild_id,show_key,character_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_episode_tasks (
            guild_id TEXT NOT NULL,show_key TEXT NOT NULL,episode INTEGER NOT NULL,
            seat TEXT NOT NULL,task_id INTEGER NOT NULL,
            PRIMARY KEY(guild_id,show_key,episode,seat))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_variety_airings (
            guild_id TEXT NOT NULL,show_key TEXT NOT NULL,episode INTEGER NOT NULL,
            performance REAL NOT NULL DEFAULT 1.0,
            published_by TEXT NOT NULL,published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,show_key,episode))''')


def _requirements(show,seat):
    key,name,subtype,lv,main,*_=show
    skill='主持' if seat=='host' else ('歌唱' if key=='music' else main)
    attrs={skill:MAIN_THRESHOLDS[seat][lv-1]}
    # 音樂競演嘉賓也可由正式管理員日後調整為舞蹈主技能。
    if key=='escape':attrs['才智']=int(attrs[skill]*0.5)
    if key=='midnight':attrs['公關']=int(attrs[skill]*0.25)
    if key=='music' and seat!='host':attrs['綜藝']=int(attrs[skill]*0.25)
    return {'occupations':list(JOBS),'min_cafe_level':CAFE_THRESHOLDS[seat][lv-1], 'min_attributes':attrs}


def _rewards(show,seat):
    key,name,subtype,lv,main,*_=show
    skill='主持' if seat=='host' else ('歌唱' if key=='music' else main)
    return {'coins':PAY[seat][lv-1],'attributes':{skill:SKILL_REWARD[seat][lv-1]}}


def _insert_task(db,gid,code,title,description,show,seat,episode=None):
    existing=db.execute('SELECT id FROM rp_task_definitions WHERE guild_id=? AND code=?',(gid,code)).fetchone()
    if existing:return int(existing['id']),False
    req=_requirements(show,seat)
    if episode is None:
        # 與一般任務驗證分離，申請時不立刻鎖定席位。
        desc=description+'｜常駐需要官方審核，錄取才取得席位。'
    else:desc=description+f'｜第 {episode} 期；先接先得。於角色劇情討論帖子完成錄製 RP 後回報。'
    cur=db.execute('''INSERT INTO rp_task_definitions
    (guild_id,code,kind,title,description,subtype,level,requirements_json,rewards_json,report_json,status)
    VALUES (?,?,?,?,?,?,?,?,?,?,'published')''',
    (gid,code,'variety',title,desc,show[2],show[3],json.dumps(req,ensure_ascii=False),
     json.dumps({} if episode is None else _rewards(show,seat),ensure_ascii=False),
     json.dumps({'mode':'character_story'},ensure_ascii=False)))
    return int(cur.lastrowid),True


def _episode(db,gid,show,number):
    key,title,subtype,level,main,hosts,residents,flights,*_=show
    if number<1 or number>show[8]:raise ValueError('超出節目已設定總期數')
    added=0
    for seat,capacity in (('host',hosts),('resident',residents),('flight',flights)):
        if not capacity:continue
        if seat=='flight':
            # 未確認原出演者所屬席位的節目先暫留一格，避免搶占既定席位。
            if key=='night': capacity=max(0,capacity-1)
            # 固定既定出演人員占位只發生在對應期，無未知類型時不臆測類別。
            reserved=db.execute('SELECT reserved_count FROM rp_variety_seats WHERE guild_id=? AND show_key=? AND seat=?',(gid,key,seat)).fetchone()
            capacity=max(0,capacity-(int(reserved['reserved_count']) if reserved else 0))
        if capacity<=0:continue
        code=f'variety_{key}_e{number}_{seat}'
        role={'host':'常駐主持','resident':'常駐嘉賓','flight':'飛行嘉賓'}[seat]
        task_id,created=_insert_task(db,gid,code,f'《{title}》第{number}期｜{role}',f'《{title}》綜藝錄製',show,seat,number)
        added+=int(created)
        db.execute('''INSERT OR IGNORE INTO rp_variety_episode_tasks
          (guild_id,show_key,episode,seat,task_id) VALUES (?,?,?,?,?)''',(gid,key,number,seat,task_id))
        if created:
            db.execute('''INSERT INTO rp_screen_cast_slots (guild_id,task_id,production_key,slot_key,capacity)
            VALUES (?,?,?,?,?)''',(gid,task_id,f'variety_{key}_e{number}',seat,capacity))
    return added


def seed_variety_tasks(guild_id):
    initialize_variety()
    gid=str(guild_id);added=0
    with transaction() as db:
        for show in SHOWS:
            key,title,subtype,lv,main,hosts,residents,flights,eps,days,oldhosts,oldresidents,oldflights=show
            db.execute('''INSERT OR IGNORE INTO rp_variety_shows
            (guild_id,show_key,title,subtype,level,main_skill,total_episodes,game_days_per_episode)
            VALUES (?,?,?,?,?,?,?,?)''',(gid,key,title,subtype,lv,main,eps,days))
            for seat,capacity,incumbents in (('host',hosts,oldhosts),('resident',residents,oldresidents),('flight',flights,oldflights)):
                if capacity<=0:continue
                # 夜行劇團已知一位出演者，但未確認常駐或飛行；兩種席位都保守預留一席。
                if key=='night' and seat in ('resident','flight'):
                    capacity=max(0,capacity-1)
                db.execute('INSERT OR IGNORE INTO rp_variety_seats(guild_id,show_key,seat,capacity,reserved_count) VALUES (?,?,?,?,?)',
                           (gid,key,seat,capacity,len(incumbents)))
                for incumbent in incumbents:
                    db.execute('INSERT OR IGNORE INTO rp_variety_fixed_cast(guild_id,show_key,artist,seat) VALUES (?,?,?,?)',
                               (gid,key,incumbent,seat))
                if seat!='flight' and len(incumbents)<capacity:
                    code=f'variety_{key}_apply_{seat}'
                    role='常駐主持' if seat=='host' else '常駐嘉賓'
                    _,made=_insert_task(db,gid,code,f'《{title}》{role}招募',f'《{title}》多期合約申請',show,seat)
                    added+=int(made)
            for unassigned in UNASSIGNED.get(key,()):
                db.execute('INSERT OR IGNORE INTO rp_variety_fixed_cast(guild_id,show_key,artist,seat) VALUES (?,?,?,?)',
                           (gid,key,unassigned,'unassigned'))
            added+=_episode(db,gid,show,1)
    return added


def publish_episode(guild_id,show_key,episode):
    initialize_variety();show=SHOW_BY_KEY.get(show_key)
    if not show:raise ValueError('未知節目')
    with transaction() as db:
        return _episode(db,str(guild_id),show,int(episode))
