"""寵物醫療 V2.3 臨床擴充：診斷、病房管理、探病、延期、病歷。

沿用 pet_medical_v23 的價目表、付款及康復規則；不修改保單或角色資料。
"""
from __future__ import annotations

import json
import random
from datetime import timedelta

from core.database import transaction
from core import pet_medical_v23 as medical, pets_v1 as pets

WARD = ('ward_basic', 'ward_comfort', 'ward_icu', 'ward_isolation')

VISIT_EFFECTS = {
    'visit': ('一般探病', 5, 0),
    'comfort': ('陪伴安撫', 0, -5),
    'play': ('溫柔互動', 3, -2),
}

DISEASES = {
    '貓': {
        'illness': ['輕微消化不適', '皮膚發炎', '呼吸道感染', '泌尿系統疾病'],
        'injury': ['輕微擦傷', '扭傷', '骨折', '嚴重創傷'],
    },
    '狗': {
        'illness': ['腸胃不適', '耳部發炎', '皮膚感染', '急性重症'],
        'injury': ['皮外傷', '扭傷', '骨折', '嚴重創傷'],
    },
    '鸚鵡': {
        'illness': ['消化不適', '足部炎症', '呼吸道感染', '急性重症'],
        'injury': ['輕微擦傷', '翅膀扭傷', '骨折', '嚴重創傷'],
    },
}


def setup(db):
    medical.setup(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_clinical_diagnoses_v23 (
      guild_id TEXT NOT NULL, case_id INTEGER NOT NULL, pet_id INTEGER NOT NULL,
      diagnosis TEXT NOT NULL, symptoms TEXT NOT NULL, discovered_at TEXT NOT NULL,
      PRIMARY KEY(guild_id,case_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_clinical_events_v23 (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      pet_id INTEGER NOT NULL, case_id INTEGER, actor_character_id INTEGER,
      event_type TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}',
      request_key TEXT, created_at TEXT NOT NULL,
      UNIQUE(guild_id,request_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_clinical_visits_v23 (
      guild_id TEXT NOT NULL,pet_id INTEGER NOT NULL, character_id INTEGER NOT NULL,
      reward_bucket TEXT NOT NULL, PRIMARY KEY(guild_id,pet_id,character_id,reward_bucket))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_pet_clinical_stay_changes_v23 (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      stay_id INTEGER NOT NULL, kind TEXT NOT NULL, old_ward TEXT,
      new_ward TEXT, old_until TEXT, new_until TEXT,
      delta INTEGER NOT NULL DEFAULT 0, invoice_id INTEGER,
      created_at TEXT NOT NULL, request_key TEXT NOT NULL,
      UNIQUE(guild_id,request_key))''')


def _auth(db, gid, pid, cid, uid):
    pets._actor(db, gid, cid, uid)
    pets._access(db, gid, pid, cid)
    return pets._pet(db, gid, pid)


def _pending(db, gid, pid):
    """Returns one currently admitted stay, including expired prepaid coverage."""
    setup(db)
    return db.execute('''SELECT * FROM rp_pet_medical_stays_v23
      WHERE guild_id=? AND pet_id=? AND discharged_at IS NULL
      ORDER BY id DESC LIMIT 1''', (str(gid), int(pid))).fetchone()


def _event(db, gid, pid, cid, kind, detail=None, *, key=None, case_id=None):
    db.execute('''INSERT OR IGNORE INTO rp_pet_clinical_events_v23
      (guild_id,pet_id,case_id,actor_character_id,event_type,
       detail_json,request_key,created_at)
      VALUES (?,?,?,?,?,?,?,?)''', (
          str(gid), int(pid), case_id, int(cid) if cid is not None else None,
          kind, json.dumps(detail or {}, ensure_ascii=False), key,
          medical.timestamp(medical.utcnow())
      ))


def diagnose(guild_id, pet_id, character_id, user_id, *, rng=None):
    """Existing injury/sickness becomes one actual diagnosed case; never invents sickness."""
    gid, pid, cid = str(guild_id), int(pet_id), int(character_id)
    rng = rng or random.SystemRandom()
    with transaction() as db:
        setup(db)
        p = _auth(db, gid, pid, cid, user_id)
        if p['state'] in ('missing', 'deceased'):
            raise ValueError('寵物目前無法接受一般門診')
        kind = 'injury' if p['illness'] == 'injured' else 'illness' if p['illness'] == 'sick' else None
        if not kind:
            raise ValueError('目前沒有待診斷傷病；健康檢查請從一般健檢入口進行')
        existing = db.execute('''SELECT * FROM rp_pet_medical_cases_v23
          WHERE guild_id=? AND pet_id=? AND illness_type=? AND status!='closed'
          ORDER BY id DESC LIMIT 1''', (gid, pid, kind)).fetchone()
        if existing:
            diagnosis = db.execute('''SELECT diagnosis,symptoms FROM rp_pet_clinical_diagnoses_v23
              WHERE guild_id=? AND case_id=?''', (gid, existing['id'])).fetchone()
            return {
                'case_id': int(existing['id']),
                'kind': kind,
                'severity': int(existing['severity']),
                'diagnosis': diagnosis['diagnosis'] if diagnosis else '已建檔傷病',
                'symptoms': diagnosis['symptoms'] if diagnosis else '需持續觀察'
            }
        # 嚴重度依健康及實際事件傷害為主，僅保留小幅隨機變動。
        evidence = db.execute('''SELECT detail_json FROM rp_pet_events_v1
          WHERE guild_id=? AND pet_id=? AND event_type IN ('sick','injured')
          ORDER BY id DESC LIMIT 1''',(gid,pid)).fetchone()
        try:
            damage = max(0,int(json.loads(evidence['detail_json']).get('health_lost',0))) if evidence else 0
        except (ValueError,TypeError,KeyError):
            damage = 0
        health = int(p['health'])
        level = (4 if health <= 15 or damage >= 35 else
                 3 if health <= 35 or damage >= 25 else
                 2 if health <= 70 or damage >= 15 else 1)
        if rng.randint(1,100) <= 10:
            level = max(1,min(4,level + rng.choice((-1,1))))
        name = DISEASES.get(p['species'], DISEASES['狗'])[kind][level - 1]
        symptoms = [
            '精神略差',
            '不適且活動減少',
            '食慾和活動明顯下降',
            '生命徵象需緊急評估'
        ][level - 1]
        onset = db.execute('''SELECT created_at FROM rp_pet_events_v1
          WHERE guild_id=? AND pet_id=? AND event_type IN ('sick','injured')
          ORDER BY id DESC LIMIT 1''', (gid, pid)).fetchone()
        t = medical.timestamp(medical.utcnow())
        cur = db.execute('''INSERT INTO rp_pet_medical_cases_v23
          (guild_id,pet_id,illness_type,severity,onset_at,diagnosed_at)
          VALUES (?,?,?,?,?,?)''', (
            gid, pid, kind, level,
            onset['created_at'] if onset else t, t
        ))
        case_id = int(cur.lastrowid)
        db.execute('''INSERT INTO rp_pet_clinical_diagnoses_v23
          VALUES (?,?,?,?,?,?)''', (gid, case_id, pid, name, symptoms, t))
        _event(
            db, gid, pid, cid, 'diagnosis',
            {'name': name, 'level': level},
            key=f'diagnosis:{case_id}',
            case_id=case_id
        )
        return {
            'case_id': case_id,
            'kind': kind,
            'severity': level,
            'diagnosis': name,
            'symptoms': symptoms
        }


def case_info(guild_id, pet_id, character_id, user_id):
    gid, pid = str(guild_id), int(pet_id)
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, character_id, user_id)
        rows = db.execute('''SELECT c.*,d.diagnosis,d.symptoms
          FROM rp_pet_medical_cases_v23 c
          LEFT JOIN rp_pet_clinical_diagnoses_v23 d
          ON d.guild_id=c.guild_id AND d.case_id=c.id
          WHERE c.guild_id=? AND c.pet_id=?
          ORDER BY c.id DESC LIMIT 20''', (gid, pid)).fetchall()
        return [dict(r) for r in rows]


def quote_treatment(case, *, surgery=None, extra=()):
    level = int(case['severity'])
    if surgery:
        if medical.PRICES.get(surgery, ('', 0, ''))[2] != 'surgery':
            raise ValueError('無效的手術項目')
        codes = [surgery]
    else:
        codes = [f'{case["illness_type"]}_{level}']
    codes.extend(extra)
    return medical.quote(codes)


def treat(
    guild_id, pet_id, character_id, user_id, case_id, request_key,
    *, surgery=None, extra=()
):
    gid, pid = str(guild_id), int(pet_id)
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, character_id, user_id)
        c = db.execute('''SELECT * FROM rp_pet_medical_cases_v23
          WHERE guild_id=? AND id=? AND pet_id=?''',
          (gid, int(case_id), pid)).fetchone()
        if not c:
            raise LookupError('找不到醫療案件')
        quote_treatment(c, surgery=surgery, extra=extra)
        codes = [surgery] if surgery else [
            f'{c["illness_type"]}_{c["severity"]}'
        ]
        codes.extend(extra)
    return medical.pay(
        gid, pid, character_id, user_id,
        codes, request_key, case_id=case_id
    )


def admit(
    guild_id, pet_id, character_id, user_id, ward, days, request_key,
    *, case_id=None
):
    gid, pid = str(guild_id), int(pet_id)
    days = int(days)
    if ward not in WARD or not 1 <= days <= 30:
        raise ValueError('病房或預付天數不正確')
    with transaction() as db:
        setup(db)
        p = _auth(db, gid, pid, character_id, user_id)
        if _pending(db, gid, pid):
            raise ValueError('目前已在住院，請使用續住或轉病房')
        if p['state'] in ('missing', 'deceased'):
            raise ValueError('目前無法安排住院')
        if case_id is not None:
            c = db.execute('''SELECT severity,status
              FROM rp_pet_medical_cases_v23
              WHERE guild_id=? AND id=? AND pet_id=?''',
              (gid, int(case_id), pid)).fetchone()
            if not c or c['status'] == 'closed':
                raise ValueError('沒有需要住院的有效傷病案件')
            if ward == 'ward_icu' and int(c['severity']) < 4:
                raise ValueError('ICU 僅適用醫療評估的危急病例')
        elif ward in ('ward_icu', 'ward_isolation'):
            raise ValueError('ICU／隔離病房需要有效的醫療案件')
    return medical.pay(
        gid, pid, character_id, user_id,
        [ward], request_key, case_id=case_id, ward_days=days
    )


def stay_info(guild_id, pet_id, character_id, user_id):
    gid, pid = str(guild_id), int(pet_id)
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, character_id, user_id)
        row = _pending(db, gid, pid)
        return dict(row) if row else None


def visit(guild_id, pet_id, character_id, user_id, action='visit'):
    gid, pid, cid = str(guild_id), int(pet_id), int(character_id)
    if action not in VISIT_EFFECTS:
        raise ValueError('沒有這個探病選項')
    with transaction() as db:
        setup(db)
        p = _auth(db, gid, pid, cid, user_id)
        if not _pending(db, gid, pid):
            raise ValueError('寵物目前沒有住院')
        bucket = str(int(medical.utcnow().timestamp() // (12 * 3600)))
        old = db.execute('''SELECT 1 FROM rp_pet_clinical_visits_v23
          WHERE guild_id=? AND pet_id=? AND character_id=?
          AND reward_bucket=?''', (gid, pid, cid, bucket)).fetchone()
        if old:
            raise ValueError(
                '12 小時內已取得探病獎勵，可以繼續 RP 但不重複增加數值'
            )
        db.execute(
            'INSERT INTO rp_pet_clinical_visits_v23 VALUES (?,?,?,?)',
            (gid, pid, cid, bucket)
        )
        name, bond, anger_delta = VISIT_EFFECTS[action]
        db.execute('''UPDATE rp_pets_v1
          SET bond=min(200,bond+?)
          WHERE guild_id=? AND id=?''', (bond, gid, pid))
        try:
            from core.pet_traits_v22 import initialize
            initialize(db)
            db.execute('''UPDATE rp_pet_traits_v22
              SET anger=max(0,anger+?)
              WHERE guild_id=? AND pet_id=?''',
              (anger_delta, gid, pid))
        except ImportError:
            pass
        _event(db, gid, pid, cid, 'visit', {'action': action})
        return {
            'action': name,
            'bond_plus': bond,
            'anger_delta': anger_delta,
            'health': int(p['health'])
        }


def _change(
    db, gid, stay, kind, key, delta, *,
    old_until=None, new_until=None, old_ward=None,
    new_ward=None, invoice_id=None
):
    db.execute('''INSERT INTO rp_pet_clinical_stay_changes_v23
      (guild_id,stay_id,kind,old_ward,new_ward,old_until,new_until,
       delta,invoice_id,created_at,request_key)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
        gid, stay['id'], kind, old_ward, new_ward,
        old_until, new_until, delta, invoice_id,
        medical.timestamp(medical.utcnow()), str(key)
    ))


def extend(guild_id, pet_id, character_id, user_id, days, request_key):
    gid, pid, cid = str(guild_id), int(pet_id), int(character_id)
    days = int(days)
    if not 1 <= days <= 30:
        raise ValueError('續住天數須為1～30天')
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, cid, user_id)
        previous = db.execute('''SELECT delta,new_until
          FROM rp_pet_clinical_stay_changes_v23
          WHERE guild_id=? AND request_key=?''',
          (gid, str(request_key))).fetchone()
        if previous:
            return {
                'total': int(previous['delta']),
                'covered_until': previous['new_until'],
                'repeated': True
            }
        stay = _pending(db, gid, pid)
        if not stay:
            raise ValueError('目前沒有住院，無法續住')
        old = medical.parse(stay['covered_until'])
        new = max(old, medical.utcnow()) + timedelta(days=days)
        cost = int(medical.PRICES[stay['ward_code']][1]) * days
        tx = db.execute('''INSERT INTO rp_pet_medical_invoices_v23
          (guild_id,pet_id,character_id,case_id,request_key,
           invoice_json,total,paid,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)''',
          (
              gid, pid, cid, stay['case_id'],
              str(request_key), '{}', cost, cost,
              medical.timestamp(medical.utcnow())
          ))
        invoice_id = int(tx.lastrowid)
        pets._pay(
            db, gid, cid, cost,
            f'pet:clinical:extend:{gid}:{invoice_id}',
            '寵物住院續住'
        )
        db.execute('''UPDATE rp_pet_medical_stays_v23
          SET covered_until=? WHERE id=?''',
          (medical.timestamp(new), stay['id']))
        detail = {
            'invoice_id': invoice_id,
            'total': cost,
            'covered_until': medical.timestamp(new),
            'ward': stay['ward_code']
        }
        db.execute('''UPDATE rp_pet_medical_invoices_v23
          SET invoice_json=? WHERE id=?''',
          (json.dumps(detail, ensure_ascii=False), invoice_id))
        _change(
            db, gid, stay, 'extend', request_key, cost,
            old_until=stay['covered_until'],
            new_until=medical.timestamp(new),
            old_ward=stay['ward_code'],
            new_ward=stay['ward_code'],
            invoice_id=invoice_id
        )
        return detail


def transfer_ward(
    guild_id, pet_id, character_id, user_id, new_ward, request_key
):
    gid, pid, cid = str(guild_id), int(pet_id), int(character_id)
    if new_ward not in WARD:
        raise ValueError('病房類型錯誤')
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, cid, user_id)
        old = db.execute('''SELECT delta,new_ward
          FROM rp_pet_clinical_stay_changes_v23
          WHERE guild_id=? AND request_key=?''',
          (gid, str(request_key))).fetchone()
        if old:
            return {
                'total': old['delta'],
                'ward': old['new_ward'],
                'repeated': True
            }
        stay = _pending(db, gid, pid)
        if not stay:
            raise ValueError('目前沒有住院')
        if stay['ward_code'] == new_ward:
            raise ValueError('已經是這種病房')
        if new_ward in ('ward_icu', 'ward_isolation'):
            if not stay['case_id']:
                raise ValueError('ICU／隔離病房需醫療案件')
            case = db.execute('''SELECT severity
              FROM rp_pet_medical_cases_v23
              WHERE guild_id=? AND id=?''',
              (gid, stay['case_id'])).fetchone()
            if not case or (
                new_ward == 'ward_icu'
                and int(case['severity']) < 4
            ):
                raise ValueError('病情不符合 ICU 適用條件')
        hours = max(
            0,
            (
                medical.parse(stay['covered_until']) - medical.utcnow()
            ).total_seconds() / 3600
        )
        old_hourly = medical.PRICES[stay['ward_code']][1] / 24
        new_hourly = medical.PRICES[new_ward][1] / 24
        delta = round((new_hourly - old_hourly) * hours)
        if delta > 0:
            pets._pay(
                db, gid, cid, delta,
                f'pet:clinical:transfer:{gid}:{request_key}',
                '寵物住院病房差額'
            )
        elif delta < 0:
            from core.database import get_or_create_account
            from core.economy import _payment
            source = get_or_create_account(
                db, gid, 'system', 'pet_center', 'wallet'
            )
            destination = get_or_create_account(
                db, gid, 'character', cid, 'wallet'
            )
            _payment(
                db, gid,
                f'pet:clinical:refund:{gid}:{request_key}',
                source, destination, -delta,
                '寵物病房降級退差額'
            )
        db.execute(
            'UPDATE rp_pet_medical_stays_v23 SET ward_code=? WHERE id=?',
            (new_ward, stay['id'])
        )
        _change(
            db, gid, stay, 'transfer', request_key, delta,
            old_ward=stay['ward_code'],
            new_ward=new_ward,
            old_until=stay['covered_until'],
            new_until=stay['covered_until']
        )
        _event(
            db, gid, pid, cid, 'transfer_ward',
            {
                'from': stay['ward_code'],
                'to': new_ward,
                'difference': delta
            },
            key=request_key
        )
        return {
            'total': delta,
            'ward': new_ward,
            'hours_left': round(hours, 2)
        }


def discharge(guild_id, pet_id, character_id, user_id, request_key):
    gid, pid, cid = str(guild_id), int(pet_id), int(character_id)
    with transaction() as db:
        setup(db)
        p = _auth(db, gid, pid, cid, user_id)
        old = db.execute('''SELECT detail_json
          FROM rp_pet_clinical_events_v23
          WHERE guild_id=? AND request_key=?''',
          (gid, str(request_key))).fetchone()
        if old:
            return json.loads(old['detail_json'])
        stay = _pending(db, gid, pid)
        if not stay:
            raise ValueError('寵物未住院')
        if p['state'] in ('critical', 'deceased') or int(p['health']) <= 0:
            raise ValueError('危急狀態不可直接出院')
        if stay['case_id']:
            c = db.execute('''SELECT severity,status
              FROM rp_pet_medical_cases_v23
              WHERE guild_id=? AND id=?''',
              (gid, stay['case_id'])).fetchone()
            if (
                c
                and c['status'] == 'diagnosed'
                and int(c['severity']) >= 3
            ):
                raise ValueError('重大傷病仍未完成必要治療')
        t = medical.timestamp(medical.utcnow())
        cost = medical.PRICES['discharge_check'][1]
        invoice_id = db.execute('''INSERT INTO rp_pet_medical_invoices_v23
          (guild_id,pet_id,character_id,case_id,request_key,
           invoice_json,total,paid,created_at)
          VALUES (?,?,?,?,?,?,?,?,?)''',
          (
              gid, pid, cid, stay['case_id'],
              str(request_key), '{}', cost, cost, t
          )).lastrowid
        pets._pay(
            db, gid, cid, cost,
            f'pet:clinical:discharge:{gid}:{invoice_id}',
            '寵物出院檢查'
        )
        db.execute('''UPDATE rp_pet_medical_invoices_v23
          SET invoice_json=? WHERE id=?''',
          (
              json.dumps(
                  {
                      'invoice_id': invoice_id,
                      'total': cost,
                      'service': 'discharge_check'
                  },
                  ensure_ascii=False
              ),
              invoice_id
          )
        )
        db.execute(
            'UPDATE rp_pet_medical_stays_v23 SET discharged_at=? WHERE id=?',
            (t, stay['id'])
        )
        result = {
            'pet_id': pid,
            'stay_id': int(stay['id']),
            'discharged_at': t,
            'total': cost,
            'invoice_id': invoice_id,
            'status': '居家康復' if stay['case_id'] else '已出院'
        }
        _event(
            db, gid, pid, cid, 'discharge',
            result, key=request_key, case_id=stay['case_id']
        )
        return result


def postpone_followup(
    guild_id, pet_id, character_id, user_id, reason
):
    gid, pid = str(guild_id), int(pet_id)
    if reason not in ('missing', 'hospitalized', 'critical', 'system'):
        raise ValueError('延期原因不符合免費複檢延期條件')
    with transaction() as db:
        setup(db)
        p = _auth(db, gid, pid, character_id, user_id)
        followup = db.execute('''SELECT *
          FROM rp_pet_first_check_v23
          WHERE guild_id=? AND pet_id=?''',
          (gid, pid)).fetchone()
        if not followup or followup['completed_at']:
            raise ValueError('沒有需要延期的首次複檢')
        if reason == 'missing' and p['state'] != 'missing':
            raise ValueError('寵物目前沒有走失')
        if reason == 'hospitalized' and not _pending(db, gid, pid):
            raise ValueError('寵物目前沒有住院')
        if reason == 'critical' and p['state'] != 'critical':
            raise ValueError('寵物目前不在危急狀態')
        if reason == 'system':
            raise PermissionError('系統維護延期須由管理端處理')
        new_deadline = max(
            medical.parse(followup['deadline_at']),
            medical.utcnow()
        ) + timedelta(days=5)
        db.execute('''UPDATE rp_pet_first_check_v23
          SET extended_until=?,status='extended'
          WHERE guild_id=? AND pet_id=?''',
          (medical.timestamp(new_deadline), gid, pid))
        _event(
            db, gid, pid, character_id, 'followup_extended',
            {
                'reason': reason,
                'until': medical.timestamp(new_deadline)
            }
        )
        return {
            'reason': reason,
            'deadline_at': medical.timestamp(new_deadline)
        }


def history(
    guild_id, pet_id, character_id, user_id, limit=15
):
    gid, pid = str(guild_id), int(pet_id)
    with transaction() as db:
        setup(db)
        _auth(db, gid, pid, character_id, user_id)
        invoices = [
            dict(x) for x in db.execute(
                '''SELECT id,case_id,total,paid,insurance_paid,created_at
                  FROM rp_pet_medical_invoices_v23
                  WHERE guild_id=? AND pet_id=?
                  ORDER BY id DESC LIMIT ?''',
                (gid, pid, min(50, max(1, int(limit))))
            )
        ]
        events = [
            dict(x) for x in db.execute(
                '''SELECT id,event_type,detail_json,created_at
                  FROM rp_pet_clinical_events_v23
                  WHERE guild_id=? AND pet_id=?
                  ORDER BY id DESC LIMIT ?''',
                (gid, pid, min(50, max(1, int(limit))))
            )
        ]
        return {
            'invoices': invoices,
            'events': events
        }
