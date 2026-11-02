
"""路人初始資料：對齊正式十三項屬性（一般5項／藝能8項）。

不得以咖位隨機加成影響指數；影響指數固定初始20。
"""
from __future__ import annotations
import random
from core.attribute_validation import calculate_values
from modules.attributes import default_schema

MAIN_SKILLS = ("演繹", "舞蹈", "歌唱", "綜藝", "主持")
TRACKS = {
    "演員": "演繹", "舞者": "舞蹈", "歌手": "歌唱", "偶像": "歌唱",
    "模特兒": "演繹", "導演": "演繹", "編劇": "演繹",
    "主持人": "主持", "配音員": "演繹", "編舞師": "舞蹈",
    "製作人": "演繹", "綜藝藝人": "綜藝", "網紅": "綜藝",
}

def _allocate(keys, total, *, cap=130):
    result = {k: 0 for k in keys}
    candidates = list(keys)
    while total:
        eligible = [k for k in candidates if result[k] < cap]
        if not eligible:
            raise ValueError("屬性池容量不足")
        k = random.choice(eligible)
        step = min(total, random.randint(1, 20), cap - result[k])
        result[k] += step
        total -= step
    return result

def generate_profile(age, job, level, gender):
    schema = default_schema()
    main = TRACKS.get(job, random.choice(MAIN_SKILLS))
    general = _allocate([k for k in schema["general"] if k != "影響指數"], 500)
    general["影響指數"] = 0
    talent = _allocate(schema["talent"], 500, cap=115 if main in schema["talent"] else 130)
    # 正式驗證器保證初始值上限、主攻加成與影響指數限制。
    result = calculate_values(schema, main, general, talent)
    years = random.randint(0, max(0, int(age) - 5))
    return {
        "gender": "男" if gender == "male" else "女",
        "experience_years": years,
        "main_track": main,
        "secondary_track": "",  # 現有正式規則沒有副賽道
        "initial_general_points": general,
        "initial_talent_points": talent,
        "extra_initial_attributes": result,
        "extra_initial_coins": max(0, int(level)-1)*500,
        "extra_initial_fans": max(0, int(level)-1)*100,
    }

def write_profile(db, guild_id, cid, profile):
    for key, value in profile["extra_initial_attributes"].items():
        db.execute("""INSERT INTO attributes(guild_id,character_id,name,value)
            VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,name)
            DO UPDATE SET value=excluded.value""", (str(guild_id), int(cid), key, value))
    # 初始金幣與粉絲需由各自的正式帳本/粉絲服務發放，不能冒充已入帳。
