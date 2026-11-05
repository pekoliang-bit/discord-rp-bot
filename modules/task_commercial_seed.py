"""低門檻一次性商演／廣告／展覽通告預設資料。

與正式公司品牌代言合約分離；只有每次演出／站台的固定報酬和單項屬性獎勵。
反覆接取以現實冷卻時間限制，已上架作品保持管理員修改內容。
"""
from __future__ import annotations

import json
from core.database import transaction
from modules.task_repository import initialize

SUBTYPE = '商業短工'
# code, 名稱, 單項門檻屬性, 最低數值, 金幣, 成長量, 現實冷卻小時, 開放職業
JOBS = (
    ('mall_show', '商場開幕商演', '歌唱', 200, 2500, 10, 24, ('歌手','偶像','演員','網紅')),
    ('plaza_dance', '商圈快閃舞台', '舞蹈', 180, 2200, 8, 24, ('舞者','偶像','歌手','演員')),
    ('exhibition_model', '展覽會場模特兒', '容貌', 150, 2000, 8, 24, ('模特兒','演員','偶像','網紅')),
    ('auto_model', '車展展示模特兒', '容貌', 250, 3200, 12, 24, ('模特兒','演員','偶像','網紅')),
    ('fashion_runway', '小型服裝走秀', '容貌', 300, 4000, 15, 36, ('模特兒','偶像','演員')),
    ('brand_photo', '品牌平面型錄拍攝', '容貌', 200, 2500, 10, 24, ('模特兒','演員','偶像','網紅')),
    ('product_demo', '新品體驗展示', '公關', 100, 1200, 5, 12, ('網紅','主持人','演員','偶像','模特兒')),
    ('opening_guest', '新店開幕站台', '公關', 180, 2400, 8, 24, ('網紅','偶像','演員','主持人','歌手')),
    ('store_live', '品牌門市一日主持', '主持', 250, 3200, 12, 24, ('主持人','網紅','偶像','演員')),
    ('fair_host', '展覽活動司儀', '主持', 300, 4000, 15, 36, ('主持人','演員','偶像','網紅')),
    ('ad_extra', '商品廣告背景演出', '演繹', 100, 1200, 5, 12, ('演員','模特兒','偶像','歌手','網紅','舞者')),
    ('ad_line', '地方品牌廣告短台詞', '演繹', 250, 3000, 12, 24, ('演員','偶像','網紅','模特兒')),
    ('street_promo', '街頭活動宣傳演出', '綜藝', 120, 1500, 6, 12, ('網紅','偶像','演員','歌手','主持人')),
    ('booth_games', '展場互動遊戲帶領', '綜藝', 220, 2600, 10, 24, ('網紅','主持人','偶像','演員')),
    ('jingle', '商業短歌錄製', '歌唱', 300, 4000, 15, 36, ('歌手','偶像','演員')),
    ('dance_promo', '促銷宣傳短舞', '舞蹈', 300, 3800, 15, 36, ('舞者','偶像','歌手')),
    ('photo_booth', '品牌拍照互動嘉賓', '容貌', 100, 1400, 5, 12, ('模特兒','偶像','演員','網紅')),
    ('product_talk', '新品發布會介紹員', '主持', 180, 2200, 8, 24, ('主持人','網紅','演員','偶像')),
    ('pop_up_guest', '快閃店一日嘉賓', '公關', 300, 4200, 15, 36, ('偶像','演員','歌手','網紅','主持人')),
    ('expo_cosplay', '動漫展主題角色演出', '演繹', 180, 2300, 8, 24, ('演員','偶像','模特兒','網紅')),
)
JOBS_BY_CODE = {f'commercial_basic_{job[0]}': job for job in JOBS}

def seed_commercial_tasks(guild_id):
    """只新增不存在的預設，避免覆蓋停用、改價和官方修改。"""
    initialize()
    gid = str(guild_id)
    added = 0
    with transaction() as db:
        for code, name, attribute, minimum, coins, gain, cooldown, occupations in JOBS:
            task_code = 'commercial_basic_' + code
            exists = db.execute('SELECT 1 FROM rp_task_definitions WHERE guild_id=? AND code=?',
                                (gid,task_code)).fetchone()
            if exists:
                continue
            req = {'occupations':list(occupations),'min_attributes':{attribute:minimum}}
            rew = {'coins':coins,'attributes':{attribute:gain}}
            desc = (f'【商業短工】{name}。單次活動，非正式品牌代言，不提供代言頭銜、自由點數或影響指數。'
                    f'符合條件可接取，完成後於本人「劇情討論」帖子回報，經審核結算。'
                    f'再次接取需間隔現實 {cooldown} 小時。')
            db.execute("""INSERT INTO rp_task_definitions
              (guild_id,code,kind,title,description,subtype,level,requirements_json,rewards_json,report_json,status)
              VALUES (?,?,?,?,?,?,?,?,?,?,'published')""",
              (gid,task_code,'endorsement',name,desc,SUBTYPE,1,
               json.dumps(req,ensure_ascii=False),json.dumps(rew,ensure_ascii=False),
               json.dumps({'mode':'character_story'},ensure_ascii=False)))
            added += 1
    return added
