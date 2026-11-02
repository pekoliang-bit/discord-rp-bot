"""任務接取、資格判定及回報服務。

獎勵發放尚未連接資產服務：審核完成只記錄 completed，不改動金幣與背包。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.database import get_connection, transaction
from modules.task_conditions import evaluate, get_profile
from modules.task_newbie import character_owner, is_graduated
from modules.task_repository import initialize, list_tasks, accept_task

VISIBLE_KINDS = ('daily', 'screen', 'variety', 'endorsement', 'limited', 'special')


def character_info(guild_id, character_id, user_id):
    if not character_owner(guild_id, character_id, user_id):
        raise ValueError('角色不存在、未啟用，或並非持有人')
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM characters WHERE guild_id=? AND id=?",
            (str(guild_id), int(character_id)),
        ).fetchone()
        columns = {r['name'] for r in db.execute('PRAGMA table_info(attributes)').fetchall()}
        attrs = {}
        if {'guild_id', 'character_id', 'name', 'value'} <= columns:
            attrs = {r['name']: r['value'] for r in db.execute(
                'SELECT name, value FROM attributes WHERE guild_id=? AND character_id=?',
                (str(guild_id), int(character_id)),
            )}
    info = dict(row)
    return {
        'occupation': info.get('occupation'),
        'age': info.get('age'),
        'attributes': attrs,
        # 不猜測既有咖位的資料來源；門檻含咖位時拒絕直到正式接線。
        'cafe_level': None,
    }


def eligible(guild_id, task, character):
    rules = json.loads(task.get('requirements_json') or '{}')
    if task['kind'] in ('screen', 'variety', 'endorsement'):
        try:
            base = get_profile(guild_id, task['subtype'], task['level'])
        except ValueError as exc:
            return {'eligible': False, 'missing': [str(exc)]}
        rules = {**base, **rules}
        if 'min_attributes' in base or 'min_attributes' in rules:
            rules['min_attributes'] = {
                **base.get('min_attributes', {}),
                **json.loads(task.get('requirements_json') or '{}').get('min_attributes', {}),
            }
    if rules.get('min_cafe_level') is not None and character.get('cafe_level') is None:
        return {'eligible': False, 'missing': ['咖位讀取尚未接線，暫停接取']}
    return evaluate(rules, character)


def board(guild_id, character_id, user_id, kind):
    initialize()
    if kind not in VISIBLE_KINDS:
        raise ValueError('未知任務分類')
    if not character_owner(guild_id, character_id, user_id):
        raise ValueError('角色身分不符')
    if not is_graduated(guild_id, character_id):
        return []
    actor = character_info(guild_id, character_id, user_id)
    tasks = [t for t in list_tasks(guild_id, kind) if eligible(guild_id, t, actor)['eligible']]
    if kind == 'daily':
        from modules.task_daily import get_daily_slots
        issued = {r['task_id'] for r in get_daily_slots(guild_id, character_id, user_id)['tasks']}
        tasks = [t for t in tasks if t['id'] in issued]
    return tasks


def accept(guild_id, character_id, user_id, task_id):
    initialize()
    if not is_graduated(guild_id, character_id):
        raise ValueError('請先完成新手任務')
    actor = character_info(guild_id, character_id, user_id)
    with get_connection() as db:
        row = db.execute(
            "SELECT * FROM rp_task_definitions WHERE guild_id=? AND id=? AND status='published'",
            (str(guild_id), int(task_id)),
        ).fetchone()
    if not row or row['kind'] == 'newbie':
        raise ValueError('此任務不存在或尚未開放')
    task = dict(row)
    now = datetime.now(timezone.utc).isoformat()
    if (task['starts_at'] and task['starts_at'] > now) or (task['ends_at'] and task['ends_at'] <= now):
        raise ValueError('任務不在開放時間')
    check = eligible(guild_id, task, actor)
    if not check['eligible']:
        raise ValueError('資格不符：' + '；'.join(check['missing']))
    cycle_key = 'once'
    if task['kind'] == 'daily':
        from modules.task_daily import current_cycle, get_daily_slots
        slots = get_daily_slots(guild_id, character_id, user_id)
        if int(task_id) not in {int(r['task_id']) for r in slots['tasks']}:
            raise ValueError('這項日常任務不在當期五項清單中')
        cycle_key = current_cycle()[0]
    return accept_task(guild_id, character_id, task_id, cycle_key=cycle_key)


def report(guild_id, character_id, user_id, task_id, channel_id):
    """在管理員指定的討論串回報，僅進入待審核；不自行認定已完成。"""
    if not character_owner(guild_id, character_id, user_id):
        raise ValueError('角色身分不符')
    with transaction() as db:
        row = db.execute('''SELECT * FROM rp_task_progress
            WHERE guild_id=? AND character_id=? AND task_id=? AND status='accepted'
            ORDER BY accepted_at DESC LIMIT 1''',
            (str(guild_id), int(character_id), int(task_id))).fetchone()
        if not row:
            raise ValueError('沒有可回報的已接取任務')
        snapshot = json.loads(row['snapshot_json'])
        if snapshot['kind'] == 'daily':
            from modules.task_daily import current_cycle
            if row['cycle_key'] != current_cycle()[0]:
                raise ValueError('這一期日常任務已過期')
        settings = json.loads(snapshot.get('report_json') or '{}')
        required = settings.get('channel_id')
        if required is None:
            raise ValueError('尚未設定可信任的回報討論串，無法提交')
        if str(required) != str(channel_id):
            raise ValueError('請在任務指定的討論串回報')
        db.execute('''UPDATE rp_task_progress SET status='submitted'
            WHERE guild_id=? AND character_id=? AND task_id=? AND cycle_key=? AND status='accepted' ''',
            (str(guild_id), int(character_id), int(task_id), row['cycle_key']))
    return True


def pending_rewards(guild_id, character_id):
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT * FROM rp_task_progress
            WHERE guild_id=? AND character_id=? AND status='completed' ''',
            (str(guild_id), int(character_id)))]
