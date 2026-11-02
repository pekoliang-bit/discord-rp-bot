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
        if not r: raise ValueError('角色不存在、未啟用或不是持有人')
        attrs={x['name']:x['value'] for x in db.execute('SELECT name,value FROM attributes WHERE guild_id=? AND character_id=?',(str(guild_id),int(character_id)))}
    return {'occupation':r['occupation'],'age':r['age'],'attributes':attrs,'cafe_level':0}

def eligible(guild_id, task, character):
    rules=json.loads(task['requirements_json'] or '{}')
    if task['kind'] in ('screen','variety','endorsement'):
        try: base=get_profile(guild_id,task['subtype'],task['level'])
        except ValueError as e:return {'eligible':False,'missing':[str(e)]}
        merged={**base,**rules}
    else: merged=rules
    # 未連接咖位讀取：有咖位要求時拒絕，不使用臆測的 0 當真值。
    if merged.get('min_cafe_level') is not None:
        return {'eligible':False,'missing':['咖位資料尚未接線，暫停接取']}
    return evaluate(merged,character)

def board(guild_id,character_id,user_id,kind):
    initialize()
    if kind not in VISIBLE_KINDS: raise ValueError('未知任務類型')
    if not character_owner(guild_id,character_id,user_id): raise ValueError('角色身分不符')
    if not is_graduated(guild_id,character_id): return []
    character=character_info(guild_id,character_id,user_id)
    return [t for t in list_tasks(guild_id,kind) if eligible(guild_id,t,character)['eligible']]

def accept(guild_id,character_id,user_id,task_id):
    initialize()
    if not is_graduated(guild_id,character_id):raise ValueError('請先完成新手任務')
    char=character_info(guild_id,character_id,user_id)
    with get_connection() as db:
        row=db.execute('SELECT * FROM rp_task_definitions WHERE guild_id=? AND id=? AND status="published"',(str(guild_id),int(task_id))).fetchone()
    if not row or row['kind']=='newbie': raise ValueError('通告未開放')
    t=dict(row)
    now=datetime.now(timezone.utc).isoformat()
    if (t['starts_at'] and t['starts_at']>now) or (t['ends_at'] and t['ends_at']<=now):raise ValueError('通告尚未開始或已結束')
    check=eligible(guild_id,t,char)
    if not check['eligible']:raise ValueError('資格不符：'+'；'.join(check['missing']))
    return accept_task(guild_id,character_id,task_id)

def report(guild_id,character_id,user_id,task_id,channel_id):
    """回報只能送審；自動判定需來源事件橋接驗證，不憑空標記完成。"""
    if not character_owner(guild_id,character_id,user_id): raise ValueError('角色身分不符')
    with transaction() as db:
        r=db.execute('SELECT * FROM rp_task_progress WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key="once"',(str(guild_id),int(character_id),int(task_id))).fetchone()
        if not r or r['status']!='accepted':raise ValueError('沒有可回報的已接取任務')
        snapshot=json.loads(r['snapshot_json'])
        settings=json.loads(snapshot.get('report_json') or '{}')
        required=settings.get('channel_id')
        if not required: raise ValueError('此任務尚未設定可信任的回報帖子，暫停回報')
        if str(required)!=str(channel_id):raise ValueError('請在指定討論串回報')
        db.execute('UPDATE rp_task_progress SET status="submitted" WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key="once"',(str(guild_id),int(character_id),int(task_id)))
    return True

def review(guild_id,character_id,task_id,approve):
    with transaction() as db:
        row=db.execute("SELECT status FROM rp_task_progress WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key='once'",(str(guild_id),int(character_id),int(task_id))).fetchone()
        if not row or row['status']!='submitted':raise ValueError('沒有待審核回報')
        if approve:
            db.execute("UPDATE rp_task_progress SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key='once'",(str(guild_id),int(character_id),int(task_id)))
        else:
            db.execute("UPDATE rp_task_progress SET status='accepted' WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key='once'",(str(guild_id),int(character_id),int(task_id)))
    return True

def pending_rewards(guild_id,character_id):
    """完成≠發獎；待正式金流及背包適配器安裝後才可領取。"""
    with get_connection() as db:
        return [dict(r) for r in db.execute("SELECT * FROM rp_task_progress WHERE guild_id=? AND character_id=? AND status='completed'",(str(guild_id),int(character_id)))]
