"""任務接取、資格判斷、回報與安全結算（獎勵待接線前不可發放）。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from core.database import transaction, get_connection
from modules.task_repository import initialize, list_tasks, accept_task
from modules.task_conditions import get_profile, evaluate
from modules.task_newbie import is_graduated, character_owner

VISIBLE_KINDS=('daily','screen','variety','endorsement','limited','special')

def character_info(guild_id, character_id, user_id):
    with get_connection() as db:
        r=db.execute("SELECT * FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status IN ('active','approved')",(str(guild_id),int(character_id),str(user_id))).fetchone()
        if not r or r['character_type']!='player': raise ValueError('角色不存在、未啟用或不是持有人')
        attrs={x['name']:x['value'] for x in db.execute('SELECT name,value FROM attributes WHERE guild_id=? AND character_id=?',(str(guild_id),int(character_id)))}
        # The role's official social standing is maintained by character_status.
        status_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_character_status'").fetchone()
        standing=None
        if status_table:
            standing_row=db.execute("SELECT level FROM rp_character_status WHERE guild_id=? AND character_id=?",
                                    (str(guild_id),int(character_id))).fetchone()
            if standing_row is not None:
                standing=int(standing_row['level'])
    return {'character_id':int(character_id),'occupation':r['occupation'],
            'age':r['age'],'attributes':attrs,'cafe_level':standing}

def eligible(guild_id, task, character):
    rules=json.loads(task['requirements_json'] or '{}')
    if task['kind'] in ('screen','variety','endorsement'):
        try: base=get_profile(guild_id,task['subtype'],task['level'])
        except ValueError as e:return {'eligible':False,'missing':[str(e)]}
        merged={**base,**rules, 'min_attributes': {**base.get('min_attributes', {}), **rules.get('min_attributes', {})}}
    else: merged=rules
    if merged.get('min_cafe_level') is not None and character.get('cafe_level') is None:
        return {'eligible':False,'missing':['角色尚未建立正式咖位資料，請先初始化角色地位']}
    return evaluate(merged,character)

def board(guild_id,character_id,user_id,kind):
    initialize()
    if kind not in VISIBLE_KINDS: raise ValueError('未知任務類型')
    if not character_owner(guild_id,character_id,user_id): raise ValueError('角色身分不符')
    if not is_graduated(guild_id,character_id): return []
    character=character_info(guild_id,character_id,user_id)
    if kind=='daily':
        from modules.task_daily import get_daily_slots
        slots=get_daily_slots(guild_id,character_id,user_id)
        ids={int(r['task_id']) for r in slots['tasks']}
        return [t for t in list_tasks(guild_id,kind) if int(t['id']) in ids and eligible(guild_id,t,character)['eligible']]
    return [t for t in list_tasks(guild_id,kind) if not (kind=='screen' and int(t['level'])>3) and eligible(guild_id,t,character)['eligible']]

def accept(guild_id,character_id,user_id,task_id):
    initialize()
    if not is_graduated(guild_id,character_id):raise ValueError('請先完成新手任務')
    char=character_info(guild_id,character_id,user_id)
    with get_connection() as db:
        row=db.execute('SELECT * FROM rp_task_definitions WHERE guild_id=? AND id=? AND status="published"',(str(guild_id),int(task_id))).fetchone()
    if not row or row['kind']=='newbie': raise ValueError('通告未開放')
    if row['kind']=='screen' and int(row['level'])>3: raise ValueError('第 4～5 級影視作品尚未開放')
    t=dict(row)
    now=datetime.now(timezone.utc).isoformat()
    if (t['starts_at'] and t['starts_at']>now) or (t['ends_at'] and t['ends_at']<=now):raise ValueError('通告尚未開始或已結束')
    check=eligible(guild_id,t,char)
    if not check['eligible']:raise ValueError('資格不符：'+'；'.join(check['missing']))
    cycle='once'
    if str(t.get('code','')).startswith('screen_basic_'):
        # 冷卻期按真實時間計算，且不能同時接取同一個未結算的基礎工作。
        from datetime import timedelta
        from modules.task_screen_seed import BASE_JOBS
        key=t['code'][len('screen_basic_'):]
        hours=next((j[6] for j in BASE_JOBS if j[0]==key),24)
        with get_connection() as db:
            previous=db.execute('''SELECT accepted_at,status FROM rp_task_progress
              WHERE guild_id=? AND character_id=? AND task_id=?
              ORDER BY accepted_at DESC LIMIT 1''',
              (str(guild_id),int(character_id),int(task_id))).fetchone()
        if previous:
            if previous['status'] in ('accepted','submitted','completed'):
                raise ValueError('此基礎工作尚未完成結算，不能重複接取')
            t0=datetime.fromisoformat(previous['accepted_at'].replace('Z','+00:00'))
            if t0.tzinfo is None:t0=t0.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc)-t0<timedelta(hours=hours):
                raise ValueError(f'此基礎工作需間隔 {hours} 小時才能再次接取')
        cycle='basic:'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    if t['kind']=='daily':
        from modules.task_daily import current_cycle, daily_task_in_current_cycle
        if not daily_task_in_current_cycle(guild_id,character_id,task_id):
            raise ValueError('此任務不在本期五項日常任務中')
        cycle=current_cycle()[0]
    return accept_task(guild_id,character_id,task_id,cycle_key=cycle)

def report(guild_id,character_id,user_id,task_id,channel_id,*,evidence=None):
    # 送審前核對 Discord 留言證據。

    evidence: {'message_id','author_id','channel_id','created_at','content'}
    # Evidence must come from Discord history, checked in task_evidence.py.
    if not character_owner(guild_id,character_id,user_id): raise ValueError('角色身分不符')
    if not is_graduated(guild_id,character_id): raise ValueError('請先完成新手任務')
    from modules.task_evidence import validate_evidence
    with transaction() as db:
        r=db.execute('''SELECT p.* FROM rp_task_progress p
          JOIN rp_task_definitions t ON t.id=p.task_id AND t.guild_id=p.guild_id
          WHERE p.guild_id=? AND p.character_id=? AND p.task_id=?
          AND p.status='accepted' ORDER BY p.accepted_at DESC LIMIT 1''',
          (str(guild_id),int(character_id),int(task_id))).fetchone()
        if not r: raise ValueError('沒有可回報的已接取任務')
        snapshot=json.loads(r['snapshot_json'])
        settings=json.loads(snapshot.get('report_json') or '{}')
        if settings.get('mode','manual') not in ('manual', 'character_story'):
            raise ValueError('此任務應由系統自動結算，不接受人工回報')
        required=settings.get('channel_id')
        if settings.get('mode') == 'character_story':
            link=db.execute("SELECT story_id FROM character_forum_links WHERE guild_id=? AND character_id=?",
                            (str(guild_id), int(character_id))).fetchone()
            required=link['story_id'] if link else None
            if not required:
                raise ValueError('尚未建立角色專屬「劇情討論」帖子，請聯絡管理員')
        if not required or str(required)!=str(channel_id):
            raise ValueError('請在通告指定的討論串回報')
        if snapshot.get('kind')=='daily':
            from modules.task_daily import current_cycle
            if r['cycle_key']!=current_cycle()[0]:
                raise ValueError('本期日常任務已過期')
        verified=validate_evidence(evidence, user_id, channel_id, r['accepted_at'])
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_evidence (
            guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL, cycle_key TEXT NOT NULL,
            message_id TEXT NOT NULL, channel_id TEXT NOT NULL,
            author_id TEXT NOT NULL, excerpt TEXT NOT NULL,
            reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id,character_id,task_id,cycle_key))''')
        db.execute('''INSERT INTO rp_task_evidence
          (guild_id,character_id,task_id,cycle_key,message_id,channel_id,author_id,excerpt)
          VALUES (?,?,?,?,?,?,?,?)
          ON CONFLICT(guild_id,character_id,task_id,cycle_key)
          DO UPDATE SET message_id=excluded.message_id,
             channel_id=excluded.channel_id, author_id=excluded.author_id,
             excerpt=excluded.excerpt, reported_at=CURRENT_TIMESTAMP''',
          (str(guild_id),int(character_id),int(task_id),r['cycle_key'],
           verified['message_id'],str(channel_id),str(user_id),verified['excerpt']))
        changed=db.execute('''UPDATE rp_task_progress SET status='submitted'
          WHERE guild_id=? AND character_id=? AND task_id=?
          AND cycle_key=? AND status='accepted' ''',
          (str(guild_id),int(character_id),int(task_id),r['cycle_key']))
        if changed.rowcount!=1:raise ValueError('任務狀態已變更，請重新載入')
    return True

def review(*args, **kwargs):
    """Legacy direct approval is disabled; all approvals require task_review.decide."""
    raise PermissionError('請由授權的 /官方 → 任務管理 → 審核回報處理')

def pending_rewards(guild_id,character_id):
    """完成≠發獎；待正式金流及背包適配器安裝後才可領取。"""
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT * FROM rp_task_progress WHERE guild_id=? AND character_id=? AND status='completed'",(str(guild_id),int(character_id)))]
