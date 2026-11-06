# 官方共用演藝工作檔期。所有時間為明確含時區的 ISO8601。
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from core.database import get_connection, transaction

WORK_KINDS = frozenset({'screen', 'variety', 'endorsement', 'limited', 'special'})

def _iso(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try: parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        except (TypeError, ValueError) as exc: raise ValueError('檔期時間請用 ISO 格式，例如 2026-11-20T09:00+08:00') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('檔期必須指定時區，例如 +08:00，不得使用無時區日期')
    return parsed.astimezone(timezone.utc).isoformat(timespec='seconds')

def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_work_task_schedule (
        guild_id TEXT NOT NULL, task_id INTEGER NOT NULL,
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
        updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id, task_id), CHECK(starts_at < ends_at))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_work_reservations (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL, cycle_key TEXT NOT NULL,
        title TEXT NOT NULL, kind TEXT NOT NULL,
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'booked',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,character_id,task_id,cycle_key),
        CHECK(state IN ('booked','cancelled')),
        CHECK(starts_at < ends_at))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_work_busy ON rp_work_reservations(guild_id, character_id, starts_at, ends_at, state)')

def set_task_schedule(guild_id, task_id, start, end, actor_id):
    begin, finish = _iso(start), _iso(end)
    if begin >= finish: raise ValueError('工作結束時間必須晚於開始時間')
    if finish <= datetime.now(timezone.utc).isoformat(timespec='seconds'):
        raise ValueError('不接受已經結束的工作檔期')
    with transaction() as db:
        ensure(db)
        task=db.execute('SELECT kind,status,code FROM rp_task_definitions WHERE guild_id=? AND id=?',(str(guild_id),int(task_id))).fetchone()
        if not task or task['kind'] not in WORK_KINDS:
            raise ValueError('此任務不存在或不屬於工作通告')
        if task['status']=='archived': raise ValueError('已封存通告不能重新安排日期')
        if str(task['code']).startswith(('screen_basic_','commercial_basic_')):
            raise ValueError('可重複接取的基礎短工使用冷卻週期，不適合設定單一固定檔期')
        prior=db.execute('''SELECT 1 FROM rp_task_progress
            WHERE guild_id=? AND task_id=? AND status NOT IN ('expired') LIMIT 1''',
            (str(guild_id),int(task_id))).fetchone()
        if prior:raise ValueError('此通告已有接取紀錄，不能直接補填或更改正式檔期')
        booked=db.execute('SELECT 1 FROM rp_work_reservations WHERE guild_id=? AND task_id=? AND state="booked" LIMIT 1',(str(guild_id),int(task_id))).fetchone()
        if booked: raise ValueError('已有角色預約此通告；避免改動既有契約，請先處理原預約')
        db.execute('''INSERT INTO rp_work_task_schedule(guild_id,task_id,starts_at,ends_at,updated_by)
            VALUES (?,?,?,?,?) ON CONFLICT(guild_id,task_id) DO UPDATE SET
            starts_at=excluded.starts_at,ends_at=excluded.ends_at,
            updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
            (str(guild_id),int(task_id),begin,finish,str(actor_id)))
    return begin,finish

def clear_task_schedule(guild_id,task_id):
    with transaction() as db:
        ensure(db)
        booked=db.execute('SELECT 1 FROM rp_work_reservations WHERE guild_id=? AND task_id=? AND state="booked" LIMIT 1',(str(guild_id),int(task_id))).fetchone()
        if booked: raise ValueError('已有角色預約此通告，無法清空已確認檔期')
        return db.execute('DELETE FROM rp_work_task_schedule WHERE guild_id=? AND task_id=?',(str(guild_id),int(task_id))).rowcount>0

def book_in_transaction(db, guild_id, character_id, task, cycle_key):
    # 同 IMMEDIATE 交易接任務並鎖檔期。
    if task['kind'] not in WORK_KINDS:return False
    ensure(db)
    slot=db.execute('SELECT starts_at,ends_at FROM rp_work_task_schedule WHERE guild_id=? AND task_id=?',
                    (str(guild_id),int(task['id']))).fetchone()
    if slot is None:return False  # 既有無正式日程任務維持可接，但不虛構已鎖檔期。
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    if slot['ends_at']<=now: raise ValueError('此通告的正式工作檔期已結束')
    conflict=db.execute('''SELECT title,starts_at,ends_at FROM rp_work_reservations
        WHERE guild_id=? AND character_id=? AND state='booked'
        AND ends_at>? AND starts_at<? AND ends_at>? LIMIT 1''',
        (str(guild_id),int(character_id),slot['starts_at'],slot['ends_at'],now)).fetchone()
    if conflict: raise ValueError('檔期衝突：已預約「'+str(conflict['title'])+'」（'+conflict['starts_at']+' ～ '+conflict['ends_at']+' UTC）')
    db.execute('''INSERT INTO rp_work_reservations
        (guild_id,character_id,task_id,cycle_key,title,kind,starts_at,ends_at)
        VALUES (?,?,?,?,?,?,?,?)''',
        (str(guild_id),int(character_id),int(task['id']),str(cycle_key),str(task['title']),str(task['kind']),slot['starts_at'],slot['ends_at']))
    return True

def upcoming(guild_id,character_id,limit=6,*,now=None):
    with get_connection() as db:
        ensure(db)
        current=_iso(now or datetime.now(timezone.utc))
        return [dict(r) for r in db.execute('''SELECT task_id,title,kind,starts_at,ends_at,state
            FROM rp_work_reservations WHERE guild_id=? AND character_id=?
            AND state='booked' AND ends_at>? ORDER BY starts_at,task_id LIMIT ?''',
            (str(guild_id),int(character_id),current,max(1,min(int(limit),20)))).fetchall()]

def task_slot(guild_id,task_id):
    with get_connection() as db:
        ensure(db)
        row=db.execute('SELECT starts_at,ends_at FROM rp_work_task_schedule WHERE guild_id=? AND task_id=?',(str(guild_id),int(task_id))).fetchone()
        return dict(row) if row else None

def cancel_reservation(guild_id,character_id,task_id,cycle_key):
    # 只允許經授權上層呼叫。
    with transaction() as db:
        ensure(db)
        return db.execute('''UPDATE rp_work_reservations SET state='cancelled'
            WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=? AND state='booked' ''',
            (str(guild_id),int(character_id),int(task_id),str(cycle_key))).rowcount==1

def display_lines(guild_id,character_id,limit=5):
    items=upcoming(guild_id,character_id,limit)
    if not items:return '目前沒有已確認的未結束工作檔期。'
    now=datetime.now(timezone.utc)
    lines=[]
    for item in items:
        start=datetime.fromisoformat(item['starts_at']).astimezone(timezone.utc)
        end=datetime.fromisoformat(item['ends_at']).astimezone(timezone.utc)
        days=max(0,int((start-now).total_seconds()//86400))
        lead='進行中' if start<=now<end else ('不到24小時' if (start-now).total_seconds()<86400 else f'約{days+1}天後')
        local_start=start.astimezone(ZoneInfo('Asia/Taipei')).strftime('%m/%d %H:%M')
        lines.append(f"#{item['task_id']} {item['title'][:38]}｜{local_start} 台灣時間｜{lead}")
    return '\n'.join(lines)[:990]


def missing_registration(guild_id, character_id, limit=5):
    """只提醒確實已接取的工作；不虛構未存在的合約或檔期。"""
    with get_connection() as db:
        ensure(db)
        rows=db.execute("""SELECT p.task_id,p.cycle_key,d.title,
            CASE WHEN t.task_id IS NULL THEN '尚未設定官方日期'
                 ELSE '有日期但未建立預約' END AS reason
            FROM rp_task_progress p
            JOIN rp_task_definitions d ON d.id=p.task_id AND d.guild_id=p.guild_id
            LEFT JOIN rp_work_task_schedule t ON t.task_id=p.task_id AND t.guild_id=p.guild_id
            LEFT JOIN rp_work_reservations r ON r.guild_id=p.guild_id
                AND r.character_id=p.character_id AND r.task_id=p.task_id
                AND r.cycle_key=p.cycle_key
            WHERE p.guild_id=? AND p.character_id=? AND p.status='accepted'
                AND (d.kind IN ('screen','variety','endorsement') OR
                     (d.kind IN ('limited','special') AND t.task_id IS NOT NULL))
                AND d.code NOT LIKE 'screen_basic_%'
                AND d.code NOT LIKE 'commercial_basic_%'
                AND r.task_id IS NULL
            ORDER BY p.task_id LIMIT ?""",
            (str(guild_id),int(character_id),max(1,min(20,int(limit))))).fetchall()
        return [dict(x) for x in rows]


def register_legacy_schedule(guild_id, task_id, start, end, actor_id):
    """官方確認過往已接工作的實際檔期，逐人防撞後原子補登記；不生造契約。"""
    begin,finish=_iso(start),_iso(end)
    if begin>=finish or finish<=datetime.now(timezone.utc).isoformat(timespec='seconds'):
        raise ValueError('補登記的正式檔期必須有效且未結束')
    gid=str(guild_id);tid=int(task_id)
    with transaction() as db:
        ensure(db)
        task=db.execute('SELECT kind,title,status,code FROM rp_task_definitions WHERE guild_id=? AND id=?',(gid,tid)).fetchone()
        if not task or task['kind'] not in WORK_KINDS or task['status']=='archived':
            raise ValueError('不存在可補登記的正式工作')
        if str(task['code']).startswith(('screen_basic_','commercial_basic_')):
            raise ValueError('可重複短工不使用單一工作檔期')
        accepted=db.execute("""SELECT character_id,cycle_key FROM rp_task_progress
            WHERE guild_id=? AND task_id=? AND status='accepted'""",(gid,tid)).fetchall()
        if not accepted: raise ValueError('找不到待補登記的已接取工作')
        # 必須與既有檔期一致，禁止悄悄覆蓋已確認的預約。
        prev=db.execute('SELECT starts_at,ends_at FROM rp_work_task_schedule WHERE guild_id=? AND task_id=?',(gid,tid)).fetchone()
        if prev and (prev['starts_at']!=begin or prev['ends_at']!=finish):
            raise ValueError('官方既有檔期不同，請先走正式改期流程')
        for rec in accepted:
            exists=db.execute('SELECT state,starts_at,ends_at FROM rp_work_reservations WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=?',
                (gid,rec['character_id'],tid,rec['cycle_key'])).fetchone()
            if exists:
                if exists['state']=='booked' and exists['starts_at']==begin and exists['ends_at']==finish:continue
                raise ValueError('已有不同或已取消的預約，不可直接補登記')
            clash=db.execute("""SELECT title FROM rp_work_reservations
                WHERE guild_id=? AND character_id=? AND state='booked'
                AND task_id<>? AND starts_at<? AND ends_at>? LIMIT 1""",
                (gid,rec['character_id'],tid,finish,begin)).fetchone()
            if clash: raise ValueError(f"角色#{rec['character_id']}與「{clash['title']}」撞期；整批補登記已取消")
        if prev is None:
            db.execute('INSERT INTO rp_work_task_schedule(guild_id,task_id,starts_at,ends_at,updated_by) VALUES(?,?,?,?,?)',(gid,tid,begin,finish,str(actor_id)))
        updated=0
        for rec in accepted:
            exists=db.execute('SELECT 1 FROM rp_work_reservations WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=?',
                (gid,rec['character_id'],tid,rec['cycle_key'])).fetchone()
            if exists:continue
            db.execute("""INSERT INTO rp_work_reservations
                (guild_id,character_id,task_id,cycle_key,title,kind,starts_at,ends_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (gid,rec['character_id'],tid,rec['cycle_key'],task['title'],task['kind'],begin,finish))
            updated+=1
        db.execute("""CREATE TABLE IF NOT EXISTS rp_work_schedule_backfill_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,task_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL,affected INTEGER NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute('INSERT INTO rp_work_schedule_backfill_audit(guild_id,task_id,actor_id,affected) VALUES(?,?,?,?)',(gid,tid,str(actor_id),updated))
        return updated
