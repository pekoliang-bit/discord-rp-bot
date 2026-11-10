"""通告按工作天數結算。只保存證據鍵、字數與進度，不複製玩家作品全文。

- 每 30 個真實日開啟新的 365 工作日額度；未使用額度不跨期累加。
- 每 10 個有效字元完成 1 天；軋戲的 50 字／天另待專案正式整合。
- 所有累積、扣額、完成標記同在 SQLite transaction，來源 ID 冪等。
- 實際獎勵由現有 task_rewards.settle_reward 發放與補發。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import unicodedata

from core.database import transaction, get_connection

MONTH_DAYS = 30
MONTH_CAPACITY = 365
CHARS_PER_DAY = 10
MAX_FILM_DAYS = 365 * 3

BOUNDS = {
    '一般廣告': (1, 5), '電視劇': (50, 120), 'MV': (3, 15),
    '網劇': (30, 70), '電影': (180, MAX_FILM_DAYS),
    '短劇': (10, 30), '秀場': (15, 45),
    '單純音樂錄製': (1, 20), '綜藝': (1, 180),
}
ALIASES = {'廣告': '一般廣告', '音樂錄製': '單純音樂錄製', '舞台秀': '秀場', '網路劇': '網劇'}


def classify(value):
    key = str(value or '').strip()
    key = ALIASES.get(key, key)
    if key not in BOUNDS:
        raise ValueError('未知通告類型；請選擇正式分類：' + '、'.join(BOUNDS))
    return key


def validate_duration(kind, days):
    key = classify(kind)
    try:
        number = int(str(days).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError('通告天數必須為整數') from exc
    if str(number) != str(days).strip():
        raise ValueError('通告天數必須為整數')
    lower, upper = BOUNDS[key]
    if not lower <= number <= upper:
        raise ValueError(f'{key}檔期只能設定 {lower}～{upper} 天')
    return key, number


def count_characters(body):
    """中文字、英文字母、數字計字；空白、標點和 emoji 不算 RP 正文字數。"""
    return sum(unicodedata.category(ch)[0] in ('L', 'N') for ch in str(body or ''))


def ensure(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_task_duration_definitions (
      guild_id TEXT NOT NULL, task_id INTEGER NOT NULL,
      notice_type TEXT NOT NULL, duration_days INTEGER NOT NULL,
      updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,task_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_work_duration_assignments (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
      cycle_key TEXT NOT NULL, notice_type TEXT NOT NULL,
      required_days INTEGER NOT NULL, completed_days INTEGER NOT NULL DEFAULT 0,
      carried_chars INTEGER NOT NULL DEFAULT 0,
      assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,character_id,task_id,cycle_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_work_duration_sources (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
      cycle_key TEXT NOT NULL, source_id TEXT NOT NULL, chars INTEGER NOT NULL,
      body_sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,source_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_work_duration_budget (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
      anchor_at TEXT NOT NULL, spent_days INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,character_id))''')


def set_duration(guild_id, task_id, notice_type, days, actor_id):
    kind, number = validate_duration(notice_type, days)
    gid, tid = str(guild_id), int(task_id)
    with transaction() as db:
        ensure(db)
        task = db.execute('''SELECT kind,status FROM rp_task_definitions
          WHERE guild_id=? AND id=?''', (gid, tid)).fetchone()
        if task is None or task['kind'] not in ('screen','variety','endorsement','limited','special'):
            raise ValueError('只能設定正式工作通告的天數')
        # 已接取的任務保留成立當下天數，禁止追溯修改。
        db.execute('''INSERT INTO rp_task_duration_definitions
          (guild_id,task_id,notice_type,duration_days,updated_by) VALUES(?,?,?,?,?)
          ON CONFLICT(guild_id,task_id) DO UPDATE SET
          notice_type=excluded.notice_type,duration_days=excluded.duration_days,
          updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
          (gid,tid,kind,number,str(actor_id)))
    return number


def task_duration(guild_id, task_id):
    with get_connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_task_duration_definitions'").fetchone() is None:
            return None
        row=db.execute('SELECT notice_type,duration_days FROM rp_task_duration_definitions WHERE guild_id=? AND task_id=?',
                       (str(guild_id),int(task_id))).fetchone()
        return dict(row) if row else None


def bind_in_transaction(db, guild_id, character_id, task_id, cycle_key):
    """接取時凍結工作天數；未設定的舊工作維持人工審核流程。"""
    ensure(db)
    gid, cid, tid, cycle=str(guild_id),int(character_id),int(task_id),str(cycle_key)
    rule=db.execute('SELECT notice_type,duration_days FROM rp_task_duration_definitions WHERE guild_id=? AND task_id=?',(gid,tid)).fetchone()
    if not rule:
        return False
    db.execute('''INSERT OR IGNORE INTO rp_work_duration_assignments
      (guild_id,character_id,task_id,cycle_key,notice_type,required_days)
      VALUES(?,?,?,?,?,?)''',(gid,cid,tid,cycle,rule['notice_type'],rule['duration_days']))
    return True


def _budget_in_transaction(db, gid, cid, at):
    now = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    row = db.execute('SELECT anchor_at,spent_days FROM rp_work_duration_budget WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
    if row is None:
        stamp=now.isoformat()
        db.execute('INSERT INTO rp_work_duration_budget VALUES(?,?,?,0)',(gid,cid,stamp))
        return MONTH_CAPACITY, stamp
    anchor=datetime.fromisoformat(row['anchor_at'])
    if anchor.tzinfo is None:anchor=anchor.replace(tzinfo=timezone.utc)
    elapsed=max(0,(now-anchor).total_seconds())
    if elapsed >= MONTH_DAYS*86400:
        cycles=int(elapsed//(MONTH_DAYS*86400))
        anchor=anchor+timedelta(days=MONTH_DAYS*cycles)
        stamp=anchor.isoformat()
        db.execute('UPDATE rp_work_duration_budget SET anchor_at=?,spent_days=0 WHERE guild_id=? AND character_id=?',(stamp,gid,cid))
        return MONTH_CAPACITY,stamp
    return max(0,MONTH_CAPACITY-int(row['spent_days'])),anchor.isoformat()


def budget(guild_id,character_id,at=None):
    current=at or datetime.now(timezone.utc)
    with transaction() as db:
        ensure(db)
        remaining,stamp=_budget_in_transaction(db,str(guild_id),int(character_id),current)
    return {'remaining_days':remaining,'total_days':MONTH_CAPACITY,'period_start':stamp,'period_days':MONTH_DAYS}


def submit_source(guild_id,character_id,task_id,source_id,body,*,at=None):
    """確認來源為玩家本人有效留言後呼叫；回傳累積進度及是否首次完成。

    本函式不驗證 Discord 作者及帖子權限；必須由可信任的 UI 驗證後呼叫。
    """
    gid,cid,tid=str(guild_id),int(character_id),int(task_id)
    key=str(source_id).strip()
    if not key or len(key)>200:raise ValueError('回報來源識別碼無效')
    char_count=count_characters(body)
    if char_count<=0:raise ValueError('沒有可計算的正文')
    current=at or datetime.now(timezone.utc)
    finished=False
    with transaction() as db:
        ensure(db)
        row=db.execute('''SELECT a.*,p.status FROM rp_work_duration_assignments a
         JOIN rp_task_progress p ON p.guild_id=a.guild_id AND p.character_id=a.character_id
           AND p.task_id=a.task_id AND p.cycle_key=a.cycle_key
         WHERE a.guild_id=? AND a.character_id=? AND a.task_id=?
           AND p.status IN ('accepted','completed','claimed')
         ORDER BY p.accepted_at DESC LIMIT 1''',(gid,cid,tid)).fetchone()
        if row is None:raise ValueError('找不到已接取且已設定天數的通告')
        cycle=row['cycle_key']
        existing=db.execute('SELECT character_id,task_id,cycle_key,chars FROM rp_work_duration_sources WHERE guild_id=? AND source_id=?',(gid,key)).fetchone()
        if existing:
            if (int(existing['character_id']),int(existing['task_id']),str(existing['cycle_key']))!=(cid,tid,cycle):
                raise ValueError('這份正文已在其他通告使用，不能重複計算')
        elif row['status'] in ('completed','claimed'):
            raise ValueError('通告已完成，不接受新回報')
        if existing:
            added=0
        else:
            digest=hashlib.sha256(str(body).encode()).hexdigest()
            previous_copy=db.execute('''SELECT 1 FROM rp_work_duration_sources WHERE guild_id=?
                AND character_id=? AND task_id=? AND cycle_key=? AND body_sha256=? LIMIT 1''',
                (gid,cid,tid,cycle,digest)).fetchone()
            if previous_copy:
                raise ValueError('同一份正文已用於本通告回報，不能複製重複計算')
            db.execute('INSERT INTO rp_work_duration_sources VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)',
                       (gid,cid,tid,cycle,key,char_count,digest))
            added=char_count
        previous=int(row['carried_chars'])
        required=int(row['required_days'])
        done=int(row['completed_days'])
        total=previous+added
        eligible=max(0,min(required-done,total//CHARS_PER_DAY))
        available,_=_budget_in_transaction(db,gid,cid,current)
        credited=min(eligible,available)
        consumed=credited*CHARS_PER_DAY
        next_carry=total-consumed
        if credited:
            db.execute('''UPDATE rp_work_duration_budget SET spent_days=spent_days+?
              WHERE guild_id=? AND character_id=?''',(credited,gid,cid))
        if credited or added:
            db.execute('''UPDATE rp_work_duration_assignments SET completed_days=completed_days+?,carried_chars=?
              WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=?''',
              (credited,next_carry,gid,cid,tid,cycle))
        done+=credited
        if done>=required and row['status']=='accepted':
            changed=db.execute('''UPDATE rp_task_progress SET status='completed',completed_at=CURRENT_TIMESTAMP
              WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=? AND status='accepted' ''',
              (gid,cid,tid,cycle))
            finished=changed.rowcount==1
        budget_left=max(0,available-credited)
    result={'task_id':tid,'required_days':required,'completed_days':done,
            'credited_days':credited,'remaining_days':required-done,
            'budget_remaining':budget_left,'newly_completed':finished,'duplicate':bool(existing),
            'cycle_key':cycle}
    if finished:
        from modules.task_rewards import settle_reward
        try:
            result['reward']=settle_reward(gid,cid,tid,cycle)
        except Exception as exc:
            # Completion is saved; the normal reward retry scheduler handles failure.
            result['reward_error']=str(exc)[:160]
    return result


def assignment(guild_id,character_id,task_id):
    with get_connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_work_duration_assignments'").fetchone():
            return None
        row=db.execute('''SELECT required_days,completed_days,carried_chars,cycle_key
          FROM rp_work_duration_assignments WHERE guild_id=? AND character_id=? AND task_id=?
          ORDER BY assigned_at DESC LIMIT 1''',(str(guild_id),int(character_id),int(task_id))).fetchone()
        return dict(row) if row else None


def budget_preview(guild_id,character_id,at=None):
    """唯讀顯示剩餘額度；未建立額度紀錄視為365天。"""
    now=at or datetime.now(timezone.utc)
    with get_connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_work_duration_budget'").fetchone():
            return MONTH_CAPACITY
        row=db.execute('SELECT anchor_at,spent_days FROM rp_work_duration_budget WHERE guild_id=? AND character_id=?',
                       (str(guild_id),int(character_id))).fetchone()
    if not row:return MONTH_CAPACITY
    start=datetime.fromisoformat(row['anchor_at'])
    if start.tzinfo is None:start=start.replace(tzinfo=timezone.utc)
    if (now-start).total_seconds()>=MONTH_DAYS*86400:return MONTH_CAPACITY
    return max(0,MONTH_CAPACITY-int(row['spent_days']))


def character_status_lines(guild_id,character_id,limit=8):
    """在 /查看 顯示每30天刷新之365天額度及正在工作的剩餘天數。"""
    remaining=budget_preview(guild_id,character_id)
    lines=[f'本期工作額度：{remaining}/{MONTH_CAPACITY} 天（每30天刷新）']
    with get_connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_work_duration_assignments'").fetchone():
            return '\n'.join(lines)
        rows=db.execute('''SELECT d.title,a.required_days,a.completed_days
          FROM rp_work_duration_assignments a
          JOIN rp_task_definitions d ON d.guild_id=a.guild_id AND d.id=a.task_id
          JOIN rp_task_progress p ON p.guild_id=a.guild_id AND p.character_id=a.character_id
            AND p.task_id=a.task_id AND p.cycle_key=a.cycle_key
          WHERE a.guild_id=? AND a.character_id=? AND p.status IN ('accepted','submitted')
          ORDER BY a.assigned_at DESC LIMIT ?''',(str(guild_id),int(character_id),int(limit))).fetchall()
    lines.extend(f'{r["title"][:26]}｜已完成{r["completed_days"]}/{r["required_days"]}天'
                 for r in rows)
    return '\n'.join(lines)
