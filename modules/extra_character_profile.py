"""路人初始資料：保持九項基本技能與年齡、資歷一致，不隨機增加影響指數。"""
from __future__ import annotations
import random
SKILLS=('容貌','才智','歌唱','舞蹈','演繹','主持','綜藝','時尚','創作')
TRACKS={'演員':'演繹','舞者':'舞蹈','歌手':'歌唱','模特兒':'時尚','導演':'創作','編劇':'創作','主持人':'主持','配音員':'演繹','編舞師':'舞蹈','製作人':'創作'}
def generate_profile(age,job,level,gender):
    years=random.randint(0,max(0,age-5))
    main=TRACKS.get(job,'才智'); secondary=random.choice([s for s in SKILLS if s!=main])
    # 這是路人模板，不從玩家配點池扣除，且不觸碰管制的影響指數。
    values={k:random.randint(10,min(150,40+level*4)) for k in SKILLS}
    values[main]=min(150,values[main]+20)
    return {'gender':'男' if gender=='male' else '女','experience_years':years,'main_track':main,'secondary_track':secondary,'extra_initial_skills':values,'extra_initial_coins':max(0,level-1)*500,'extra_initial_fans':max(0,level-1)*100}
def write_profile(db,guild_id,cid,profile):
    # 寫入已確認的角色技能表；沒有對應欄位時保留 JSON 供後續整合。
    cols={r[1] for r in db.execute('PRAGMA table_info(character_skills)').fetchall()}
    needed={'guild_id','character_id','skill_name','skill_value'}
    if needed<=cols:
        for name,value in profile['extra_initial_skills'].items():
            db.execute('INSERT INTO character_skills(guild_id,character_id,skill_name,skill_value) VALUES(?,?,?,?)',(guild_id,cid,name,value))
