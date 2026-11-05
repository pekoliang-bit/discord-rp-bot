"""任務 V1 資料層；不註冊 Discord 指令、不自行發獎。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from core.database import transaction, get_connection

KINDS = ('newbie','daily','screen','variety','endorsement','limited','special')

def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_definitions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
          code TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', subtype TEXT NOT NULL DEFAULT '',
          level INTEGER NOT NULL DEFAULT 1 CHECK(level BETWEEN 1 AND 5),
          requirements_json TEXT NOT NULL DEFAULT '{}', rewards_json TEXT NOT NULL DEFAULT '{}',
          report_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'draft',
          starts_at TEXT, ends_at TEXT, version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(guild_id,code), CHECK(kind IN ('newbie','daily','screen','variety','endorsement','limited','special')),
          CHECK(status IN ('draft','published','paused','archived'))
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_progress (
          guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
          cycle_key TEXT NOT NULL DEFAULT 'once', status TEXT NOT NULL DEFAULT 'accepted',
          definition_version INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
          accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT, claimed_at TEXT,
          PRIMARY KEY(guild_id,character_id,task_id,cycle_key),
          CHECK(status IN ('accepted','submitted','completed','claimed','expired'))
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_events (
          guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
          event_key TEXT NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,character_id,event_key,source_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_category_names (
          guild_id TEXT NOT NULL, kind TEXT NOT NULL, display_name TEXT NOT NULL,
          PRIMARY KEY(guild_id,kind)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_level_profiles (
          guild_id TEXT NOT NULL, subtype TEXT NOT NULL, level INTEGER NOT NULL,
          rules_json TEXT NOT NULL, PRIMARY KEY(guild_id,subtype,level),
          CHECK(level BETWEEN 1 AND 5)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_evidence (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL, cycle_key TEXT NOT NULL,
            message_id TEXT NOT NULL, channel_id TEXT NOT NULL,
            author_id TEXT NOT NULL, excerpt TEXT NOT NULL,
            reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,character_id,task_id,cycle_key))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_screen_cast_slots (
          guild_id TEXT NOT NULL, task_id INTEGER NOT NULL, production_key TEXT NOT NULL,
          slot_key TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity>=1),
          PRIMARY KEY(guild_id,task_id))''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_rp_task_board ON rp_task_definitions(guild_id,kind,status)')

def publish_task(guild_id, code, kind, title, *, description='', subtype='', level=1, requirements=None, rewards=None, report=None, status='draft', starts_at=None, ends_at=None):
    if kind not in KINDS or status not in ('draft','published','paused','archived') or not 1 <= int(level) <= 5:
        raise ValueError('非法任務類型、狀態或等級')
    if not isinstance(requirements or {},dict) or not isinstance(rewards or {},dict) or not isinstance(report or {},dict):
        raise ValueError('條件、獎勵及回報設定必須是物件')
    if not code.strip() or not title.strip():
        raise ValueError('任務識別碼及名稱不得為空')
    with transaction() as db:
        db.execute('''INSERT INTO rp_task_definitions
        (guild_id,code,kind,title,description,subtype,level,requirements_json,rewards_json,report_json,status,starts_at,ends_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(guild_id,code) DO UPDATE SET
          kind=excluded.kind,title=excluded.title,description=excluded.description,
          subtype=excluded.subtype,level=excluded.level,requirements_json=excluded.requirements_json,
          rewards_json=excluded.rewards_json,report_json=excluded.report_json,
          status=excluded.status,starts_at=excluded.starts_at,ends_at=excluded.ends_at,
          version=rp_task_definitions.version+1''',
          (str(guild_id),code,kind,title,description,subtype,int(level),
           json.dumps(requirements or {},ensure_ascii=False),json.dumps(rewards or {},ensure_ascii=False),
           json.dumps(report or {},ensure_ascii=False),status,starts_at,ends_at))

def list_tasks(guild_id, kind=None, *, now=None):
    if kind is not None and kind not in KINDS: raise ValueError('未知任務分類')
    now = now or datetime.now(timezone.utc).isoformat()
    with get_connection() as db:
        rows=db.execute('''SELECT * FROM rp_task_definitions WHERE guild_id=? AND status='published'
         AND (? IS NULL OR kind=?) AND (starts_at IS NULL OR starts_at<=?)
         AND (ends_at IS NULL OR ends_at>?) ORDER BY id''',
         (str(guild_id),kind,kind,now,now)).fetchall()
    return [dict(x) for x in rows]

def record_event(guild_id, character_id, event_key, source_id, payload=None):
    if not event_key or not source_id: raise ValueError('事件鍵及來源 ID 必填')
    with transaction() as db:
        c=db.execute('''INSERT OR IGNORE INTO rp_task_events
         (guild_id,character_id,event_key,source_id,payload_json) VALUES (?,?,?,?,?)''',
         (str(guild_id),int(character_id),event_key,str(source_id),json.dumps(payload or {},ensure_ascii=False)))
        return c.rowcount == 1

def get_events(guild_id,character_id):
    with get_connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM rp_task_events WHERE guild_id=? AND character_id=?',
                                            (str(guild_id),int(character_id))).fetchall()]

def accept_task(guild_id,character_id,task_id,cycle_key='once'):
    """接任務時凍結條件及獎勵，防止後續管理修改影響現有契約。"""
    with transaction() as db:
        row=db.execute("SELECT * FROM rp_task_definitions WHERE id=? AND guild_id=? AND status='published'",(int(task_id),str(guild_id))).fetchone()
        if not row: raise ValueError('任務未發布或不存在')
        # 同部作品每角色只能接一職位；名額在 IMMEDIATE 交易內核對。
        cast=db.execute('SELECT * FROM rp_screen_cast_slots WHERE guild_id=? AND task_id=?',
                        (str(guild_id),int(task_id))).fetchone()
        if cast:
            other=db.execute('''SELECT 1 FROM rp_task_progress p
                 JOIN rp_screen_cast_slots s ON s.guild_id=p.guild_id AND s.task_id=p.task_id
                 WHERE p.guild_id=? AND p.character_id=? AND s.production_key=?
                 AND p.status NOT IN ('expired') LIMIT 1''',
                 (str(guild_id),int(character_id),cast['production_key'])).fetchone()
            if other: raise ValueError('同一角色在本作品已接取其他演出職位')
            used=db.execute('''SELECT COUNT(*) AS cnt FROM rp_task_progress p
                 JOIN rp_screen_cast_slots s ON s.guild_id=p.guild_id AND s.task_id=p.task_id
                 WHERE p.guild_id=? AND p.task_id=? AND p.status NOT IN ('expired')''',
                 (str(guild_id),int(task_id))).fetchone()['cnt']
            if used>=cast['capacity']:raise ValueError('本職位已額滿')
        now=datetime.now(timezone.utc).isoformat()
        if (row['starts_at'] and row['starts_at']>now) or (row['ends_at'] and row['ends_at']<=now):
            raise ValueError('任務不在開放時間')
        cur=db.execute('''INSERT OR IGNORE INTO rp_task_progress
         (guild_id,character_id,task_id,cycle_key,definition_version,snapshot_json)
         VALUES (?,?,?,?,?,?)''',(str(guild_id),int(character_id),int(task_id),cycle_key,row['version'],json.dumps(dict(row),ensure_ascii=False)))
        return cur.rowcount==1


# Official editor API; all callers must check permissions before reaching this layer.
def get_definition(guild_id,task_id):
    initialize()
    with get_connection() as db:
        row=db.execute('SELECT * FROM rp_task_definitions WHERE guild_id=? AND id=?',(str(guild_id),int(task_id))).fetchone()
    return dict(row) if row else None

def admin_update(guild_id,task_id,expected_version,fields):
    """Optimistic edit: never changes current acceptances' frozen snapshot."""
    from modules.task_validation import parse_object,validate_definition
    allowed={'title','description','subtype','level','requirements_json','rewards_json','report_json','starts_at','ends_at'}
    if not fields or set(fields)-allowed:raise ValueError('不支援的欄位')
    before=get_definition(guild_id,task_id)
    if not before:raise ValueError('找不到任務')
    if before['status']=='archived':raise ValueError('封存任務禁止直接編輯')
    draft={**before,**fields}
    parsed={k:parse_object(draft[k]) for k in ('requirements_json','rewards_json','report_json')}
    validate_definition({'code':draft['code'],'title':draft['title'],'kind':draft['kind'],'subtype':draft['subtype'],
      'level':draft['level'],'requirements':parsed['requirements_json'],'rewards':parsed['rewards_json'],
      'report':parsed['report_json'],'starts_at':draft['starts_at'],'ends_at':draft['ends_at']})
    updates=[];args=[]
    for k,v in fields.items():
        if k in parsed:v=json.dumps(parsed[k],ensure_ascii=False)
        updates.append(k+'=?');args.append(v)
    with transaction() as db:
        cur=db.execute('UPDATE rp_task_definitions SET '+','.join(updates)+',version=version+1 WHERE guild_id=? AND id=? AND version=?',
                       (*args,str(guild_id),int(task_id),int(expected_version)))
        if cur.rowcount!=1:raise ValueError('此任務已被其他管理員修改，請重新開啟')
    return get_definition(guild_id,task_id)

def admin_set_status(guild_id,task_id,status,expected_version):
    from modules.task_validation import validate_definition
    if status not in ('draft','published','paused','archived'):raise ValueError('狀態錯誤')
    before=get_definition(guild_id,task_id)
    if not before:raise ValueError('找不到任務')
    if before['status']=='archived':raise ValueError('封存任務不可重新啟用')
    if status=='published':
        validate_definition({'code':before['code'],'title':before['title'],'kind':before['kind'],
          'level':before['level'],'subtype':before['subtype'],
          'requirements':json.loads(before['requirements_json']), 'rewards':json.loads(before['rewards_json']),
          'report':json.loads(before['report_json']),'starts_at':before['starts_at'],'ends_at':before['ends_at']},publish=True)
        if before['kind'] in ('screen','variety','endorsement'):
            from modules.task_conditions import get_profile
            get_profile(guild_id,before['subtype'],before['level'])
    with transaction() as db:
        cur=db.execute('UPDATE rp_task_definitions SET status=?,version=version+1 WHERE guild_id=? AND id=? AND version=?',
                        (status,str(guild_id),int(task_id),int(expected_version)))
        if cur.rowcount!=1:raise ValueError('狀態已改變，請重新開啟')
    return get_definition(guild_id,task_id)
