"""寵物醫療 V2.3：高價價目、分項帳單、保險預留與首次複檢。

所有金額為遊戲金幣。醫療付款/記錄在單一 SQLite 交易內完成。
"""
from __future__ import annotations
import json
import secrets
from datetime import datetime, timedelta, timezone
from core.database import transaction
from core import pets_v1 as pets

VERSION = '2.3'
UTC = timezone.utc

# (名稱, 定價, 類別)；免費首次複檢的服務定價與實收分開保存。
PRICES = {
 'basic_check':('基礎健康檢查',5000,'check'),
 'advanced_check':('進階健康檢查',12000,'check'),
 'full_check':('完整健康檢查',25000,'check'),
 'precision_check':('高階精密檢查',50000,'check'),
 'emergency_check':('緊急診察',15000,'check'),
 'behavior_check':('行為與情緒評估',18000,'check'),
 'postop_check':('術後追蹤檢查',8000,'check'),
 'first_followup':('首次適應期複檢',12000,'check'),
 'blood_basic':('基礎血液檢查',8000,'lab'),
 'blood_complete':('完整血液與生化檢查',18000,'lab'),
 'urine_stool':('尿液或糞便檢查',6000,'lab'),
 'skin_parasite':('皮膚與寄生蟲檢查',7000,'lab'),
 'xray':('X光檢查',15000,'lab'),
 'ultrasound':('超音波檢查',20000,'lab'),
 'ct':('電腦斷層檢查',60000,'lab'),
 'mri':('核磁共振檢查',90000,'lab'),
 'endoscopy':('內視鏡檢查',45000,'lab'),
 'pathology':('病理或特殊感染檢測',25000,'lab'),
 'illness_1':('LV1疾病基本治療',8000,'illness'),
 'illness_2':('LV2疾病基本治療',25000,'illness'),
 'illness_3':('LV3疾病基本治療',90000,'illness'),
 'illness_4':('LV4疾病基本治療',250000,'illness'),
 'injury_1':('LV1外傷基本治療',10000,'injury'),
 'injury_2':('LV2外傷基本治療',35000,'injury'),
 'injury_3':('LV3外傷基本治療',120000,'injury'),
 'injury_4':('LV4外傷基本治療',300000,'injury'),
 'medication':('一般藥物療程',5000,'medicine'),
 'medication_long':('長期藥物療程',18000,'medicine'),
 'infection_special':('特殊感染治療',30000,'medicine'),
 'nutrition_support':('營養支持治療',12000,'medicine'),
 'fluids':('輸液與支持照護',15000,'medicine'),
 'specialist':('特殊專科治療',50000,'medicine'),
 'chronic_followup':('長期疾病追蹤方案',35000,'medicine'),
 'suture':('小型傷口縫合手術',25000,'surgery'),
 'surgery_general':('一般外科手術',80000,'surgery'),
 'surgery_complex':('複雜外科手術',180000,'surgery'),
 'fracture_fix':('一般骨折固定手術',150000,'surgery'),
 'fracture_rebuild':('複雜骨折重建手術',280000,'surgery'),
 'organ_surgery':('內臟重大手術',350000,'surgery'),
 'emergency_surgery':('高風險緊急手術',500000,'surgery'),
 'sedation':('一般鎮靜',10000,'anesthesia'),
 'anesthesia_general':('全身麻醉',25000,'anesthesia'),
 'anesthesia_high':('高風險麻醉管理',50000,'anesthesia'),
 'postop_meds':('術後止痛與藥物',12000,'medicine'),
 'postop_special':('術後專項追蹤',15000,'check'),
 'ward_basic':('普通病房24小時',10000,'hospital'),
 'ward_comfort':('高級舒適病房24小時',25000,'hospital'),
 'ward_icu':('重症監護24小時',60000,'hospital'),
 'ward_isolation':('特殊隔離病房24小時',40000,'hospital'),
 'emergency_basic':('基礎急救處置',30000,'emergency'),
 'emergency_severe':('重症緊急處置',100000,'emergency'),
 'emergency_critical':('危急生命搶救',300000,'emergency'),
 'emergency_special':('高風險特殊急救',450000,'emergency'),
 'prevention_consult':('一般預防諮詢',3000,'prevention'),
 'parasite_basic':('基礎預防照護',5000,'prevention'),
 'prevention_advanced':('進階預防處理',12000,'prevention'),
 'vaccination':('適用疫苗單次施打',8000,'prevention'),
 'prevention_bundle':('完整預防檢查套組',30000,'prevention'),
 'annual_health':('年度完整健康管理',80000,'prevention'),
 'annual_premium':('高階年度健康管理',150000,'prevention'),
 'species_special':('特殊物種專科預防評估',20000,'prevention'),
 'nutrition_meal':('住院營養餐',3000,'hospital_extra'),
 'comfort_care':('住院舒適照護',5000,'hospital_extra'),
 'discharge_check':('出院檢查',8000,'check'),
}

RECOVERY = {
    1:(20,0),
    2:(15,24),
    3:(15,72),
    4:(10,120),
}


def utcnow():
    return datetime.now(UTC)


def parse(s):
    return datetime.fromisoformat(s).astimezone(UTC)


def timestamp(d):
    return d.isoformat()


def setup(db):
    pets.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_first_check_v23 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,started_at TEXT NOT NULL,
      opens_at TEXT NOT NULL,deadline_at TEXT NOT NULL,completed_at TEXT,
      extended_until TEXT, status TEXT NOT NULL DEFAULT 'pending',
      last_notice TEXT NOT NULL DEFAULT '',PRIMARY KEY(guild_id,pet_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_medical_cases_v23 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,
      illness_type TEXT NOT NULL,severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 4),
      onset_at TEXT NOT NULL,diagnosed_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'diagnosed',
      recovery_until TEXT,remaining_recovery INTEGER NOT NULL DEFAULT 0,
      last_recovery_at TEXT,closed_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_medical_invoices_v23 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,
      character_id INTEGER NOT NULL,case_id INTEGER,request_key TEXT NOT NULL,
      invoice_json TEXT NOT NULL,total INTEGER NOT NULL,paid INTEGER NOT NULL,
      insurance_paid INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'paid',
      created_at TEXT NOT NULL,UNIQUE(guild_id,request_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_medical_stays_v23 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL,
      case_id INTEGER,ward_code TEXT NOT NULL,admitted_at TEXT NOT NULL,
      covered_until TEXT NOT NULL,discharged_at TEXT,invoice_id INTEGER NOT NULL)''')


def register_first_check(db,gid,pid,when=None):
    setup(db)
    when=when or utcnow()
    gid=str(gid)
    db.execute('''INSERT OR IGNORE INTO rp_pet_first_check_v23
      (guild_id,pet_id,started_at,opens_at,deadline_at)
      VALUES (?,?,?,?,?)''',
      (
          gid,int(pid),timestamp(when),
          timestamp(when+timedelta(days=7)),
          timestamp(when+timedelta(days=12))
      )
    )


def first_check(gid,pid):
    with transaction() as db:
        setup(db)
        r=db.execute(
            '''SELECT * FROM rp_pet_first_check_v23
            WHERE guild_id=? AND pet_id=?''',
            (str(gid),int(pid))
        ).fetchone()
        return dict(r) if r else None


def quote(codes, *, free_first=False):
    if not codes or len(codes)>20:
        raise ValueError('請選擇1～20項服務')
    if len(set(codes))!=len(codes):
        raise ValueError('同一服務不可重複計價')
    if any(code not in PRICES for code in codes):
        raise ValueError('包含未上架的醫療服務')
    categories=[PRICES[x][2] for x in codes]
    if categories.count('anesthesia')>1:
        raise ValueError('一次治療只能選擇一種麻醉方案')
    if categories.count('surgery')>1:
        raise ValueError('一次醫療方案只能包含一種主手術')
    if categories.count('emergency')>1:
        raise ValueError('一次醫療方案只能選擇一種急救級別')
    if categories.count('illness')+categories.count('injury')>1:
        raise ValueError('基本治療不可重複疊加')
    if 'surgery' in categories and (
        'illness' in categories or 'injury' in categories
    ):
        raise ValueError(
            '手術已取代重疊的基礎治療費，請移除重複項目'
        )
    lines=[
        {
            'code':x,
            'name':PRICES[x][0],
            'original':PRICES[x][1],
            'paid':0 if free_first and x=='first_followup' else PRICES[x][1],
            'category':PRICES[x][2]
        }
        for x in codes
    ]
    return {
        'lines':lines,
        'original_total':sum(x['original'] for x in lines),
        'total':sum(x['paid'] for x in lines),
        'insurance_paid':0
    }


def open_case(
    guild_id,pet_id,character_id,user_id,
    illness_type='illness',severity=1
):
    if illness_type not in ('illness','injury'):
        raise ValueError('傷病類型不正確')
    severity=int(severity)
    if severity not in range(1,5):
        raise ValueError('傷病等級不正確')
    gid=str(guild_id)
    with transaction() as db:
        setup(db)
        pets._actor(db,gid,character_id,user_id)
        pets._access(db,gid,pet_id,character_id)
        p=pets._pet(db,gid,pet_id)
        if p['state']=='deceased':
            raise ValueError('無法替已故寵物建立醫療案件')
        row=db.execute(
            """SELECT id FROM rp_pet_medical_cases_v23
            WHERE guild_id=? AND pet_id=? AND illness_type=?
              AND status!='closed' LIMIT 1""",
            (gid,int(pet_id),illness_type)
        ).fetchone()
        if row:
            return int(row['id'])
        t=timestamp(utcnow())
        c=db.execute(
            '''INSERT INTO rp_pet_medical_cases_v23
            (guild_id,pet_id,illness_type,severity,onset_at,diagnosed_at)
            VALUES (?,?,?,?,?,?)''',
            (gid,int(pet_id),illness_type,severity,t,t)
        )
        return int(c.lastrowid)


def pay(
    guild_id,pet_id,character_id,user_id,
    codes,request_key,*,case_id=None,ward_days=0
):
    """支付使用者已確認的報價；重試同 request_key 不會重複收費。"""
    gid=str(guild_id)
    pid=int(pet_id)
    cid=int(character_id)
    if not str(request_key).strip():
        raise ValueError('交易識別不可留空')
    with transaction() as db:
        setup(db)
        pets._actor(db,gid,cid,user_id)
        pets._access(db,gid,pid,cid)
        previous=db.execute(
            '''SELECT invoice_json,pet_id,character_id
            FROM rp_pet_medical_invoices_v23
            WHERE guild_id=? AND request_key=?''',
            (gid,str(request_key))
        ).fetchone()
        if previous:
            if (
                int(previous['pet_id'])!=pid
                or int(previous['character_id'])!=cid
            ):
                raise PermissionError(
                    '此醫療付款編號已由其他角色或寵物使用'
                )
            return json.loads(previous['invoice_json'])
        p=pets._pet(db,gid,pid)
        if p['state'] in ('missing','deceased'):
            raise ValueError('走失或已故寵物不能安排一般醫療')
        f=db.execute(
            '''SELECT * FROM rp_pet_first_check_v23
            WHERE guild_id=? AND pet_id=?''',
            (gid,pid)
        ).fetchone()
        is_free=False
        if 'first_followup' in codes:
            if not f:
                raise ValueError('這隻寵物沒有待完成的首次複檢')
            if f['completed_at']:
                raise ValueError('首次複檢已完成')
            if utcnow()<parse(f['opens_at']):
                raise ValueError(
                    '購買或領養滿7天才開放首次複檢'
                )
            is_free=True
        days=int(ward_days)
        if days<0 or days>30:
            raise ValueError('住院天數必須介於0～30')
        wards=[
            x for x in codes
            if PRICES[x][2]=='hospital'
        ]
        if len(wards)>1 or (bool(wards)!=bool(days)):
            raise ValueError(
                '住院必須選擇一種病房及對應的天數'
            )
        if case_id is not None:
            case=db.execute(
                '''SELECT * FROM rp_pet_medical_cases_v23
                WHERE guild_id=? AND id=? AND pet_id=?''',
                (gid,int(case_id),pid)
            ).fetchone()
            if not case or case['status']=='closed':
                raise ValueError('醫療案件不存在或已結案')
        invasive={'illness','injury','surgery','emergency'}
        kinds={PRICES[x][2] for x in codes}
        if kinds.intersection(invasive):
            if case_id is None:
                raise ValueError(
                    '正式治療與急救必須綁定有效的傷病案件'
                )
            if case['status'] not in ('diagnosed',):
                raise ValueError(
                    '該案件尚未診斷，或已進入康復／結案狀態'
                )
            for x in codes:
                if PRICES[x][2] in ('illness','injury'):
                    expected=f'{case["illness_type"]}_{case["severity"]}'
                    if x!=expected:
                        raise ValueError(
                            '醫療等級必須由實際診斷決定'
                        )
        q=quote(codes,free_first=is_free)
        if wards:
            ward=next(
                x for x in q['lines']
                if x['code']==wards[0]
            )
            ward['quantity']=days
            ward['original']*=days
            ward['paid']*=days
            q['original_total']=sum(
                x['original'] for x in q['lines']
            )
            q['total']=sum(x['paid'] for x in q['lines'])
        invoice_id=db.execute(
            '''INSERT INTO rp_pet_medical_invoices_v23
            (guild_id,pet_id,character_id,case_id,request_key,
             invoice_json,total,paid,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (
                gid,pid,cid,case_id,str(request_key),
                '{}',q['total'],q['total'],timestamp(utcnow())
            )
        ).lastrowid
        pets._pay(
            db,gid,cid,q['total'],
            f'pet:medical:v23:{gid}:{invoice_id}',
            '寵物醫療帳單'
        )
        t=utcnow()
        # 窗口內完成同等或更完整的檢查，也可抵首次複檢。
        followup_equivalent=(
            is_free
            or bool(
                {
                    'advanced_check',
                    'full_check',
                    'precision_check'
                }.intersection(codes)
            )
        )
        if (
            f and not f['completed_at']
            and followup_equivalent
            and t>=parse(f['opens_at'])
        ):
            db.execute(
                '''UPDATE rp_pet_first_check_v23
                SET completed_at=?,status='completed'
                WHERE guild_id=? AND pet_id=?''',
                (timestamp(t),gid,pid)
            )
        if case_id is not None and any(
            PRICES[x][2] in (
                'illness','injury','surgery','emergency'
            )
            for x in codes
        ):
            level=int(case['severity'])
            recover,hours=RECOVERY[level]
            new_health=min(
                100,int(p['health'])+recover
            )
            state=(
                'home'
                if new_health>0 and p['state']=='critical'
                else p['state']
            )
            db.execute(
                '''UPDATE rp_pets_v1
                SET health=?,state=?,critical_since=?,illness=?
                WHERE guild_id=? AND id=?''',
                (
                    new_health,state,
                    None if state=='home' else p['critical_since'],
                    '',gid,pid
                )
            )
            db.execute(
                '''UPDATE rp_pet_medical_cases_v23
                SET status=?,recovery_until=?,
                    remaining_recovery=?,last_recovery_at=?,closed_at=?
                WHERE id=?''',
                (
                    'closed' if level==1 else 'recovering',
                    timestamp(t+timedelta(hours=hours))
                    if hours else None,
                    0 if level==1 else 20,
                    timestamp(t),
                    timestamp(t) if level==1 else None,
                    int(case_id)
                )
            )
        if wards:
            db.execute(
                '''INSERT INTO rp_pet_medical_stays_v23
                (guild_id,pet_id,case_id,ward_code,
                 admitted_at,covered_until,invoice_id)
                VALUES (?,?,?,?,?,?,?)''',
                (
                    gid,pid,case_id,wards[0],
                    timestamp(t),
                    timestamp(t+timedelta(days=days)),
                    invoice_id
                )
            )
        q.update({
            'invoice_id':invoice_id,
            'pet_id':pid,
            'case_id':case_id,
            'paid_at':timestamp(t)
        })
        db.execute(
            '''UPDATE rp_pet_medical_invoices_v23
            SET invoice_json=? WHERE id=?''',
            (
                json.dumps(q,ensure_ascii=False),
                invoice_id
            )
        )
        return q


def settle_recovery(guild_id,pet_id,*,at=None):
    gid=str(guild_id)
    pid=int(pet_id)
    at=at or utcnow()
    with transaction() as db:
        setup(db)
        p=pets._pet(db,gid,pid)
        rows=db.execute(
            '''SELECT * FROM rp_pet_medical_cases_v23
            WHERE guild_id=? AND pet_id=? AND status='recovering' ''',
            (gid,pid)
        ).fetchall()
        recovered=0
        for c in rows:
            elapsed=max(
                0,int(
                    (
                        at-parse(c['last_recovery_at'])
                    ).total_seconds()//43200
                )
            )
            if elapsed:
                gain=min(
                    int(c['remaining_recovery']),
                    elapsed*5
                )
                recovered+=gain
                remain=int(c['remaining_recovery'])-gain
                db.execute(
                    '''UPDATE rp_pet_medical_cases_v23
                    SET remaining_recovery=?,
                        last_recovery_at=?,status=?,closed_at=?
                    WHERE id=?''',
                    (
                        remain,
                        timestamp(
                            parse(c['last_recovery_at'])
                            +timedelta(hours=elapsed*12)
                        ),
                        'closed'
                        if remain==0 else 'recovering',
                        timestamp(at)
                        if remain==0 else None,
                        c['id']
                    )
                )
        if recovered:
            db.execute(
                '''UPDATE rp_pets_v1
                SET health=min(100,health+?)
                WHERE guild_id=? AND id=?''',
                (recovered,gid,pid)
            )
        return recovered


def due_notices(guild_id,*,at=None):
    """產生待通知內容；交由角色論壇通知佇列投遞。"""
    gid=str(guild_id)
    at=at or utcnow()
    results=[]
    with transaction() as db:
        setup(db)
        rows=db.execute(
            '''SELECT c.*,p.name,p.main_character_id
            FROM rp_pet_first_check_v23 c
            JOIN rp_pets_v1 p
              ON c.guild_id=p.guild_id AND c.pet_id=p.id
            WHERE c.guild_id=? AND c.completed_at IS NULL''',
            (gid,)
        ).fetchall()
        for r in rows:
            opens=parse(r['opens_at'])
            end=parse(
                r['extended_until'] or r['deadline_at']
            )
            if at>=end:
                stage='overdue'
            elif at>=end-timedelta(hours=24):
                stage='last_day'
            elif at>=opens:
                stage='open'
            else:
                continue
            previous=set(
                filter(
                    None,
                    str(r['last_notice']).split(',')
                )
            )
            if stage in previous:
                continue
            message={
                'open':'首次適應期複檢已開放',
                'last_day':'首次複檢將於24小時內截止',
                'overdue':'首次複檢已逾期，請盡快補檢'
            }[stage]
            results.append({
                'character_id':int(r['main_character_id']),
                'pet_id':int(r['pet_id']),
                'stage':stage,
                'message':(
                    f'🐾 {r["name"]}：{message}。'
                    f'截止時間：{end:%Y-%m-%d %H:%M} UTC。'
                    '請至 /寵物中心 → 健康檢查。'
                )
            })
    return results


def mark_followup_notice(guild_id,pet_id,stage):
    if stage not in ('open','last_day','overdue'):
        raise ValueError('通知狀態不正確')
    with transaction() as db:
        setup(db)
        row=db.execute(
            '''SELECT last_notice
            FROM rp_pet_first_check_v23
            WHERE guild_id=? AND pet_id=?''',
            (str(guild_id),int(pet_id))
        ).fetchone()
        if row:
            stages=set(
                filter(
                    None,
                    str(row['last_notice']).split(',')
                )
            )
            stages.add(stage)
            db.execute(
                '''UPDATE rp_pet_first_check_v23
                SET last_notice=?,status=?
                WHERE guild_id=? AND pet_id=?''',
                (
                    ','.join(sorted(stages)),
                    'overdue' if stage=='overdue' else 'pending',
                    str(guild_id),int(pet_id)
                )
            )
