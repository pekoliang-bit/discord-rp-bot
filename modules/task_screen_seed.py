"""首批已討論影視作品：只建立前三級可玩的角色通告。

先前已有資料（包含官方自行修改/停用）不覆蓋。未敲定的四、五級作品只存檔於
CATALOG，不發布。沒有拿到正式檔期及席位預約功能之前，本模組不聲稱預約了檔期。
"""
from __future__ import annotations
import json
from core.database import transaction, get_connection
from modules.task_repository import initialize
from modules.task_conditions import SCREEN_MAIN_SKILL, SCREEN_EXTRA_SKILL, SCREEN_LEAD_CAFE

# (作品代碼, 作品名稱, 類型, 暫定等級, 主演清單, 配角清單, 已佔用演員備註)
CATALOG = [
 ('night','無聲之夜','電影',3,[('男主角','沈宇峻','江硯'),('女主角','溫茜兒',None)], [('男配角','周潯振','斯特林'),('女配角','陸縵',None)]),
 ('border','無邊','電影',4,[('男主角','賀乙',None),('女主角','呂筱舒',None)], [('男配角','裴澤','斯特林'),('女配角','嚴珉敏',None)]),
 ('banquet','極樂晚宴：2','電影',5,[('男主角','顧言舟',None),('女主角','姜黎',None)], [('男配角','許魏輕',None),('女配角','方綺',None)]),
 ('bamboo','竹','電影',4,[('男主角','蕭景珩',None),('女主角','楚青黛',None)], [('男配角','霍凌霄',None),('女配角','謝宛筠',None)]),
 ('candle','燭光遊戲','電視劇',3,[('男主角','高今',None),('女主角','許意濃',None)],[('男配角','徐星辭',None),('女配角','白詩予',None)]),
 ('herbs','從採藥開始','電視劇',4,[('女主角','林小歲',None)],[('男配角','陸沉風',None),('女配角','宋千旭',None)]),
 ('summer','盛夏與蜜柑','電視劇',2,[('男主角','江馳','江硯'),('女主角','蕭雲',None)],[('男配角','陳野',None),('女配角','林溪恩',None)]),
 ('spy','往事風雲錄','電視劇',4,[('男主角','沈廷舟',None),('女主角','黎書雅',None)],[('男配角','齊遠航',None),('女配角','蘇曼',None)]),
]
MVS=[('neon','溺水霓虹',3,None),('light','借一寸光',4,'植田花繪'),
     ('dance','在末日跳一支慢舞',3,'植田花繪'),('rain','雨季',3,None),
     ('half','半衰期',2,None),('wildfire','野火燒過春天',3,None)]

# 已討論的可修改片酬基準。新作品採固定專業獎勵，不在此階段提前發放曝光/自由點數。
PAY = {
 '電影': {1:(30000,15000,7500,3000),2:(90000,45000,22500,6000),3:(270000,135000,67500,12000)},
 '電視劇':{1:(20000,10000,5000,2500),2:(60000,30000,15000,5000),3:(180000,90000,45000,10000)},
 'MV': {1:(15000,8000,6000,2000),2:(45000,24000,18000,4000),3:(135000,72000,54000,8000)},
}
ACT = {'電影': {1:(30,20,10,3),2:(60,40,20,5),3:(120,80,40,10)},
       '電視劇':{1:(25,15,8,2),2:(50,30,15,4),3:(100,60,30,8)},
       'MV': {1:(20,12,10,2),2:(40,24,20,4),3:(80,48,40,8)}}

# 製作分級在正式發行前可由官方調整；新手工作專用一次冷卻週期。
JOBS=('演員','偶像','歌手','模特兒','網紅','舞者')
BASE_JOBS=[
 ('ad_bg','廣告背景演出','電影',20,1500,2,12),
 ('ad_photo','平面廣告模特兒','電影',20,2000,2,12),
 ('mv_bg','MV 背景群演','MV',25,1500,2,12),
 ('tv_bit','電視劇龍套','電視劇',35,2500,3,24),
 ('film_extra','電影群眾演員','電影',50,3000,3,24),
 ('ad_line','廣告有台詞演員','電影',60,4000,5,24),
 ('short_bit','短片臨時演員','電影',80,5000,6,24),
 ('mv_dance','MV 伴舞','MV',35,5000,5,24),
]

def _roles():
    for key,name,typ,lv,leads,supports in CATALOG:
        if lv>3:continue
        for group,entries,idx in (('lead',leads,0),('support',supports,1)):
            for kind,role,occupied in entries:
                if occupied:continue
                yield (f'{key}:{group}:{role}',name,typ,lv,f'{kind}：{role}',group,1,idx,False)
        yield(f'{key}:featured',name,typ,lv,'特約演員','featured',4 if key in ('night','candle','summer') else 5,2,False)
        yield(f'{key}:extra',name,typ,lv,'群眾演員','extra',1000000,3,False)
    for key,name,lv,occupied in MVS:
        if lv>3:continue
        for group,label,idx,capacity in (('lead','主要演出',0,2),('support','特別出演',1,2),('featured','舞者',2,8),('extra','群演',3,1000000)):
            # 已知參與歌手保留原標記，不假定佔用主要演出職位。
            yield (f'mv_{key}:{group}',name,'MV',lv,label,group,capacity,idx, bool(occupied))

def _rule(typ,lv,group,idx,label):
    main=SCREEN_MAIN_SKILL[lv-1]
    if group=='lead': needed=main
    elif group=='support':needed=(main*3+3)//4
    elif group=='featured':needed=main//2
    else:needed=SCREEN_EXTRA_SKILL[lv-1]
    cafe=SCREEN_LEAD_CAFE[typ][lv-1]
    if group=='support':cafe=max(1,cafe-2)
    if group=='featured':cafe=max(1,cafe-4)
    if group=='extra':cafe=None
    attr={'演繹':needed}
    if typ=='MV' and group=='featured':
        attr={'舞蹈':needed,'演繹':max(1,needed//4)}
    rules={'occupations':list(JOBS),'min_attributes':attr}
    if cafe is not None:rules['min_cafe_level']=cafe
    return rules

def _insert(db,gid,code,title,desc,typ,lv,rules,rewards,report,production=None,slot=None,capacity=1):
    found=db.execute('SELECT id FROM rp_task_definitions WHERE guild_id=? AND code=?',(gid,code)).fetchone()
    if found:return False
    cur=db.execute('''INSERT INTO rp_task_definitions
    (guild_id,code,kind,title,description,subtype,level,requirements_json,rewards_json,report_json,status)
    VALUES (?,?,?,?,?,?,?,?,?,?,'published')''',
    (gid,code,'screen',title,desc,typ,lv,json.dumps(rules,ensure_ascii=False),
     json.dumps(rewards,ensure_ascii=False),json.dumps(report,ensure_ascii=False)))
    if production:
        db.execute('INSERT INTO rp_screen_cast_slots (guild_id,task_id,production_key,slot_key,capacity) VALUES (?,?,?,?,?)',
                   (gid,cur.lastrowid,production,slot,capacity))
    return True

def seed_screen_tasks(guild_id):
    initialize()
    gid=str(guild_id); created=0
    with transaction() as db:
        for code,name,typ,lv,label,group,capacity,idx,occupied_artist in _roles():
            rewards={'coins':PAY[typ][lv][idx],
                     'attributes': {'舞蹈' if typ=='MV' and group=='featured' else '演繹':ACT[typ][lv][idx]}}
            desc=f'《{name}》｜{label}。先接先得；在本人「劇情討論」帖子完成拍攝 RP 並回報，官方審核後結算。'
            if occupied_artist:desc+=' 既定歌手：植田花繪（原定參與，具體職位尚未指定）。'
            created+=_insert(db,gid,'screen_'+code.replace(':','_').replace('：','_'),f'《{name}》{label}',desc,typ,lv,
                             _rule(typ,lv,group,idx,label),rewards,{'mode':'character_story'},
                             production=code.split(':')[0],slot=group,capacity=capacity)
        for code,title,typ,need,coins,skill,cooldown in BASE_JOBS:
            attr={'舞蹈':100,'演繹':35} if code=='mv_dance' else {'演繹':need}
            created+=_insert(db,gid,'screen_basic_'+code,title,
                f'初期演藝工作；可在冷卻期後再次接取。請於本人「劇情討論」帖子發表演出 RP 並回報。',
                typ,1,{'occupations':list(JOBS),'min_cafe_level':1,'min_attributes':attr},
                {'coins':coins,'attributes':{'舞蹈' if code=='mv_dance' else '演繹':skill}},
                {'mode':'character_story'})
    return created
