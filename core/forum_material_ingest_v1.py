"""論壇素材自動收件：固定帖子 ID → 審計 / 原生素材庫 / 草稿。

未核准素材只收件，不得直接進入可抽選的正式庫。
"""
from __future__ import annotations

import json
import re
from core.database import transaction, get_connection

GUILD_ID = '1544051203493601380'
THREADS = {
    '1572145708667117578':'male_adult',
    '1572145846223642695':'female_adult',
    '1572145895515095120':'male_minor',
    '1572145958584713226':'female_minor',
    '1572146261023526983':'surname',
    '1572146330296385600':'given',
    '1572146459459985408':'jobs',
    '1572146561222443099':'tags',
    '1572147008309956669':'WN',
    '1572148191774908466':'CN',
    '1572148643828604948':'HB',
    '1572149076042981486':'HE',
    '1572149694723919872':'comments',
    '1572150674529976410':'endorsement',
    '157217752759678167':'advertising',
    '1572177757788577922':'film_tv',
    '1572177854517612575':'other_jobs',
}
TEST_CHARACTER_INDEX_ID = '1572178575904219157'
PHOTO_TYPES = {'male_adult':('male','adult'), 'female_adult':('female','adult'),
               'male_minor':('male','minor'), 'female_minor':('female','minor')}
KINDS = {'endorsement','advertising','film_tv','other_jobs'}
HIST = {'WN','CN','HB','HE'}


def setup():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_forum_material_inbox_v1 (
            guild_id TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            author_id TEXT NOT NULL, material_type TEXT NOT NULL,
            payload_json TEXT NOT NULL, state TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,message_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_forum_notice_drafts_v1 (
            guild_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
            task_id INTEGER NOT NULL, slot_index INTEGER NOT NULL,
            slot_json TEXT NOT NULL, PRIMARY KEY(guild_id,source_message_id,slot_index)
        )''')


def get_record(gid, mid):
    with get_connection() as db:
        return db.execute('SELECT state,detail FROM rp_forum_material_inbox_v1 WHERE guild_id=? AND message_id=?',
                          (str(gid),str(mid))).fetchone()


def fields(text):
    """中文標籤與單行值解析；允許同名標籤於不同招募段落。"""
    result={}
    for line in (text or '').splitlines():
        match=re.match(r'^\s*([^:：\n]{1,40})\s*[:：]\s*(.*?)\s*$',line)
        if match: result[match.group(1).strip()]=match.group(2).strip()
    return result


def require(data,*keys):
    missing=[k for k in keys if not data.get(k)]
    if missing: raise ValueError('缺少欄位：'+'、'.join(missing))


def parse(text,kind,photo_urls=()):
    text=(text or '').strip()
    if len(text)>15000:raise ValueError('素材文字超過15000字')
    data=fields(text)
    if kind in PHOTO_TYPES:
        urls=list(dict.fromkeys(photo_urls))
        if not urls:raise ValueError('請上傳 PNG/JPG/WEBP 圖片附件')
        return {'urls':urls,'note':text[:500]}
    if kind in ('surname','given'):
        require(data,'國籍', '姓氏' if kind=='surname' else '名字')
        name=data['姓氏' if kind=='surname' else '名字']
        if len(name)>80:raise ValueError('姓名過長')
        return {'nationality':data['國籍'],'name':name}
    if kind=='jobs':
        require(data,'職業')
        return {'job':data['職業']}
    if kind=='tags':
        require(data,'名稱','分類')
        return {'name':data['名稱'],'category':data['分類'],
                'effect':data.get('效果','無'), 'condition':data.get('觸發條件',''),
                'description':data.get('說明','')}
    if kind=='WN':
        require(data,'作品名稱','作品類型')
    if kind=='CN':
        require(data,'劇中姓名')
    if kind=='HB':
        require(data,'品牌名稱','代言內容','頭銜','稀有度')
    if kind=='HE':
        require(data,'名稱','經歷類型','稀有度')
    if kind in HIST:
        rarity=data.get('稀有度','')
        if rarity and rarity not in {'R1','R2','R3','R4','R5'}:
            raise ValueError('稀有度只能是 R1～R5')
        return data
    if kind=='comments':
        require(data,'主分類','情緒','評論文字')
        from core.comment_library import CATEGORIES
        raw=data['主分類'].strip()
        num=re.match(r'^(?:0?([1-9])|1([0-2]))(?:\D|$)',raw)
        category=int(num.group(1) or num.group(2)) if num else next((k for k,v in CATEGORIES.items() if v==raw),None)
        if category not in CATEGORIES:raise ValueError('主分類須為01～12或既有分類名稱')
        return {'category_id':category,'emotion':data['情緒'],'text':data['評論文字'],
                'scene':data.get('適用情境','')}
    if kind in KINDS:
        label={'endorsement':'品牌名稱','advertising':'廣告名稱','film_tv':'作品名稱','other_jobs':'通告名稱'}[kind]
        require(data,label,'招募截止')
        if not (data.get('所需檔期') or data.get('合作檔期')):
            raise ValueError('缺少欄位：所需檔期（或合作檔期）')
        sections=re.split(r'(?m)^\s*(?:【招募角色(?:與職位)?】|【招募職位】)\s*$',text,maxsplit=1)
        header=fields(sections[0])
        slots=[]
        if len(sections)>1:
            parts=re.split(r'(?m)^\s*\d+[.．、]\s*$',sections[1]);
            for part in parts:
                slot=fields(part)
                if not slot:continue
                require(slot,'職位','酬勞')
                slots.append(slot)
        if not slots:
            # 單職位的商業代言也可直接用舊格式，沒有招募角色區塊。
            slot={'職位':header.get('代言頭銜') or header.get('招募職位／人數') or header.get('招募角色／人數') or '通告人員',
                  '人數':header.get('人數','1'), '所需咖位':header.get('所需咖位','不限'),
                  '所需數值':header.get('所需數值','不限'),
                  '酬勞':header.get('代言費用') or header.get('拍攝酬勞') or header.get('酬勞','')}
            require(slot,'酬勞');slots=[slot]
        if len(slots)>50:raise ValueError('每部作品最多50個招募職位')
        for slot in slots:
            lv=(slot.get('所需咖位') or '不限').strip()
            if lv!='不限' and not re.fullmatch(r'LV(?:[1-9]|1\d|20)(?:以上|～LV(?:[1-9]|1\d|20))?',lv):
                raise ValueError('所需咖位格式請填「不限」或「LV8以上」或「LV6～LV12」')
            count=re.search(r'\d+',slot.get('人數','1'))
            if count and not 1<=int(count.group())<=100:raise ValueError('招募人數應為1～100')
        return {'heading':header,'slots':slots,'kind':kind}
    raise ValueError('未知素材類型')


def commit_material(gid,mid,tid,uid,kind,payload):
    """一次性記錄＋匯入。通告只建立草稿，不發獎／公開招募。"""
    setup()
    if get_record(gid,mid): return 'duplicate','這則留言已處理過'
    state='pending';detail='已進待審素材庫'
    if kind in PHOTO_TYPES:
        from modules import extra_character_store as x
        gender,age=PHOTO_TYPES[kind]
        count=x.add_photos(gid,payload['urls'],gender=gender,age_group=age,channel_id=tid,message_id=mid)
        state='imported';detail=f'照片入庫 {count} 張（重複照片自動跳過）'
    elif kind in ('surname','given'):
        from modules import extra_character_store as x
        count=x.add_names(gid,payload['nationality'],kind,[payload['name']]);state='imported';detail=f'姓名入庫 {count} 筆'
    elif kind=='jobs':
        from modules import extra_character_store as x
        count=x.add_jobs(gid,[payload['job']]);state='imported';detail=f'職業入庫 {count} 筆'
    elif kind=='tags':
        from modules.tag_library import init_db
        init_db()
        with transaction() as db:
            db.execute('''INSERT OR IGNORE INTO rp_tag_library(guild_id,name,category,enabled)
                VALUES (?,?,?,0)''',(str(gid),payload['name'],payload['category']))
        state='pending'; detail='標籤已建立停用候選；效果未核准，不會生效'
    elif kind in KINDS:
        from modules.task_repository import initialize,publish_task
        initialize()
        category={'film_tv':'screen','advertising':'special','endorsement':'endorsement','other_jobs':'special'}[kind]
        header=payload['heading']
        title=next((header[x] for x in ('作品名稱','廣告名稱','品牌名稱','通告名稱') if header.get(x)), '未命名通告')
        task_ids=[]
        for index,slot in enumerate(payload['slots'],1):
            code=f'forum_{mid}_{index}'
            description=json.dumps({'作品':title,'分類':kind,'基本資訊':header,'招募職位':slot},ensure_ascii=False)
            cafematch=re.search(r'LV(\d+)',slot.get('所需咖位',''))
            requirements={'min_cafe_level':int(cafematch.group(1))} if cafematch else {}
            for token in re.split('[、,，]',slot.get('所需數值','')):
                match=re.fullmatch(r'\s*([^≥\s]+)\s*≥\s*(\d+)\s*',token)
                if match:requirements.setdefault('min_attributes',{})[match.group(1)]=int(match.group(2))
            subtype=header.get('作品類型') or header.get('廣告類型') or header.get('通告類型') or kind
            with get_connection() as db:
                exists=db.execute('SELECT id FROM rp_task_definitions WHERE guild_id=? AND code=?',
                                  (str(gid),code)).fetchone()
            if not exists:
                publish_task(gid,code,category,f'{title}｜{slot["職位"]}',description=description,subtype=subtype,
                             requirements=requirements,status='draft')
            with get_connection() as db:
                row=db.execute('SELECT id FROM rp_task_definitions WHERE guild_id=? AND code=?',(str(gid),code)).fetchone()
            if row:task_ids.append((int(row['id']),index,slot))
        with transaction() as db:
            for task_id,index,slot in task_ids:
                db.execute('''INSERT OR IGNORE INTO rp_forum_notice_drafts_v1
                    (guild_id,source_message_id,task_id,slot_index,slot_json) VALUES (?,?,?,?,?)''',
                    (str(gid),str(mid),task_id,index,json.dumps(slot,ensure_ascii=False)))
        state='draft';detail=f'已建立 {len(task_ids)} 個職位通告草稿，未公開／未配置自動獎勵'
    with transaction() as db:
        db.execute('''INSERT OR IGNORE INTO rp_forum_material_inbox_v1
            (guild_id,message_id,thread_id,author_id,material_type,payload_json,state,detail)
            VALUES (?,?,?,?,?,?,?,?)''',(str(gid),str(mid),str(tid),str(uid),kind,
              json.dumps(payload,ensure_ascii=False),state,detail))
    return state,detail
