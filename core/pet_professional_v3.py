"""寵物專業訓練：正式課程報名、付款、計時、暫停、結算與退款。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

from core.database import transaction, get_or_create_account
from core.economy import _payment
from core import pets_v1 as pets
from core import pet_traits_v22 as traits

# 課程價格、時間均沿用已核准清單。
COURSES = {
    '基礎指令': (5000, 2, '基礎指令', None),
    '耐心培養': (6000, 2, '耐心培養', None),
    '環境適應': (8000, 3, '環境適應', None),
    '親密互動': (8000, 3, '親密互動', None),
    '防止破壞': (12000, 4, '防止破壞', None),
    '情緒調節': (12000, 4, '情緒調節', None),
    '益智訓練': (15000, 5, '益智訓練', None),
    '進階技能': (25000, 6, '進階技能', None),
    '貓咪定點訓練': (8000, 3, '基礎指令', '貓'),
    '貓咪外出適應': (12000, 4, '環境適應', '貓'),
    '貓咪響應名字': (10000, 3, '基礎指令', '貓'),
    '貓咪益智互動': (15000, 5, '益智訓練', '貓'),
    '貓咪美容適應': (20000, 4, '環境適應', '貓'),
    '狗狗隨行訓練': (8000, 3, '服從訓練', '狗'),
    '狗狗定點等待': (12000, 3, '服從訓練', '狗'),
    '狗狗召回訓練': (18000, 4, '服從訓練', '狗'),
    '狗狗社交禮儀': (20000, 5, '親密互動', '狗'),
    '狗狗進階指令': (30000, 6, '進階技能', '狗'),
    '鸚鵡上手訓練': (10000, 3, '基礎指令', '鸚鵡'),
    '鸚鵡回應指令': (15000, 4, '基礎指令', '鸚鵡'),
    '鸚鵡環境適應': (15000, 4, '環境適應', '鸚鵡'),
    '鸚鵡目標訓練': (20000, 5, '益智訓練', '鸚鵡'),
    '鸚鵡聲音模仿': (35000, 6, '進階技能', '鸚鵡'),
}


def setup(db):
    traits.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_professional_courses_v3 (
       id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
       pet_id INTEGER NOT NULL,character_id INTEGER NOT NULL,
       course TEXT NOT NULL,fee INTEGER NOT NULL,
       request_key TEXT NOT NULL,starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,
       state TEXT NOT NULL DEFAULT 'booked',remaining_seconds INTEGER NOT NULL DEFAULT 0,
       outcome_json TEXT,created_at TEXT NOT NULL,
       UNIQUE(guild_id,request_key))''')


def _time():
    return datetime.now(timezone.utc)


def available(species):
    return [(name,price,hours) for name,(price,hours,_,only) in COURSES.items()
            if only is None or only==species]


def book(guild_id,pet_id,character_id,user_id,course,request_key):
    gid,pid,cid=str(guild_id),int(pet_id),int(character_id)
    if course not in COURSES:raise ValueError('未知專業訓練課程')
    if not str(request_key).strip():raise ValueError('交易識別碼不可空白')
    price,hours,base,species=COURSES[course]
    with transaction() as db:
        setup(db)
        pets._actor(db,gid,cid,user_id)
        pets._access(db,gid,pid,cid)
        existing=db.execute('SELECT * FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND request_key=?',
                            (gid,request_key)).fetchone()
        if existing:
            if existing['pet_id']!=pid or existing['character_id']!=cid or existing['course']!=course:
                raise PermissionError('交易識別碼不屬於本次報名')
            return dict(existing)
        pet=pets._pet(db,gid,pid)
        if species and species!=pet['species']:raise ValueError('物種不符合課程資格')
        if pet['state']!='home' or pet['illness'] or pets._hospitalized(db,gid,pid):
            raise ValueError('寵物須在家且健康才能報名訓練')
        if db.execute("SELECT 1 FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND pet_id=? AND state IN ('booked','running','paused')",
                      (gid,pid)).fetchone():raise ValueError('已有正在進行或暫停的課程')
        # daily two-course limit on course completions, regardless of pricing.
        today=_time().date().isoformat()
        completed=db.execute("SELECT COUNT(*) FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND pet_id=? AND state='done' AND substr(ends_at,1,10)=?",(gid,pid,today)).fetchone()[0]
        if completed>=2:raise ValueError('這隻寵物今天已完成兩堂專業課程')
        start=_time()+timedelta(minutes=5)  # paid but not started grace for cancellation
        end=start+timedelta(hours=hours)
        payer=get_or_create_account(db,gid,'character',cid,'wallet')
        receiver=get_or_create_account(db,gid,'system','pet_center','wallet')
        _payment(db,gid,f'pet:professional:{gid}:{request_key}',payer,receiver,price,f'專業訓練：{course}')
        ident=db.execute('''INSERT INTO rp_pet_professional_courses_v3
          (guild_id,pet_id,character_id,course,fee,request_key,starts_at,ends_at,
           remaining_seconds,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
           (gid,pid,cid,course,price,request_key,start.isoformat(),end.isoformat(),hours*3600,_time().isoformat())).lastrowid
        return dict(db.execute('SELECT * FROM rp_pet_professional_courses_v3 WHERE id=?',(ident,)).fetchone())


def settle(guild_id,course_id,character_id,user_id):
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db)
        pets._actor(db,gid,cid,user_id)
        row=db.execute('SELECT * FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND id=? AND character_id=?',
                       (gid,int(course_id),cid)).fetchone()
        if not row:raise LookupError('找不到課程')
        if row['state']=='done':return {'status':'already','result':row['outcome_json']}
        if row['state']=='cancelled':raise ValueError('課程已取消')
        if row['state']=='paused':raise ValueError('課程暫停中')
        if _time()<datetime.fromisoformat(row['ends_at']):
            raise ValueError('尚未到達課程完成時間')
        pet=pets._pet(db,gid,row['pet_id'])
        if pet['state']!='home' or pet['illness'] or pets._hospitalized(db,gid,row['pet_id']):
            db.execute("UPDATE rp_pet_professional_courses_v3 SET state='paused' WHERE id=?",(int(course_id),))
            return {'status':'paused'}
        import json
        base=COURSES[row['course']][2]
        trial=traits._training_in_transaction(db,gid,int(row['pet_id']),cid,user_id,base,True,__import__('random').SystemRandom())
        result=json.dumps(trial,ensure_ascii=False)
        db.execute("UPDATE rp_pet_professional_courses_v3 SET state='done',outcome_json=? WHERE id=? AND state IN ('booked','running')",
                   (result,int(course_id)))
        return {'status':'done','result':trial}


def cancel(guild_id,course_id,character_id,user_id):
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db);pets._actor(db,gid,cid,user_id)
        row=db.execute('SELECT * FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND id=? AND character_id=?',
                       (gid,int(course_id),cid)).fetchone()
        if not row or row['state'] not in ('booked','running'):
            raise ValueError('課程不在可取消狀態')
        refund=int(row['fee']*.9) if _time()<datetime.fromisoformat(row['starts_at']) else 0
        if refund:
            payer=get_or_create_account(db,gid,'system','pet_center','wallet')
            receiver=get_or_create_account(db,gid,'character',cid,'wallet')
            _payment(db,gid,f'pet:professional:refund:{gid}:{course_id}',payer,receiver,refund,'課程取消退款')
        db.execute("UPDATE rp_pet_professional_courses_v3 SET state='cancelled' WHERE id=?",(int(course_id),))
        return {'refund':refund}


def resume(guild_id,course_id,character_id,user_id):
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db);pets._actor(db,gid,cid,user_id)
        row=db.execute('SELECT * FROM rp_pet_professional_courses_v3 WHERE guild_id=? AND id=? AND character_id=?',
                       (gid,int(course_id),cid)).fetchone()
        if not row or row['state']!='paused':raise ValueError('沒有暫停的課程')
        pet=pets._pet(db,gid,row['pet_id'])
        if pet['state']!='home' or pet['illness'] or pets._hospitalized(db,gid,row['pet_id']):
            raise ValueError('寵物尚未康復，無法續課')
        end=_time()+timedelta(seconds=max(0,int(row['remaining_seconds'])))
        db.execute("UPDATE rp_pet_professional_courses_v3 SET state='running',ends_at=? WHERE id=?",(end.isoformat(),int(course_id)))
        return {'ends_at':end.isoformat()}
