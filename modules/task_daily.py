"""日常任務：台灣時間每四小時固定切換，每個角色同一期最多五項。

這是資料層，無額外 slash 指令。只有已發布、符合資格的日常任務可入選。
尚未審核的預設內容不會在這裡擅自建立。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.database import get_connection, transaction
from modules.task_repository import initialize, list_tasks
from modules.task_newbie import character_owner, is_graduated
from modules.task_service import character_info, eligible

TAIPEI = ZoneInfo('Asia/Taipei')
SLOTS_PER_DAY = 6
MAX_TASKS = 5


def current_cycle(now: datetime | None = None, tz=TAIPEI):
    """回傳 (週期鍵, 週期結束時間)；以固定 00/04/08/12/16/20 時段計算。"""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now 必須含時區')
    local = now.astimezone(tz)
    start = local.replace(hour=(local.hour // 4) * 4, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=4)
    return f'{start.date().isoformat()}-{start.hour:02d}', end.isoformat()


def initialize_daily():
    initialize()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_task_daily_slots (
          guild_id TEXT NOT NULL,
          character_id INTEGER NOT NULL,
          cycle_key TEXT NOT NULL,
          task_id INTEGER NOT NULL,
          display_order INTEGER NOT NULL,
          selected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (guild_id, character_id, cycle_key, task_id),
          UNIQUE (guild_id, character_id, cycle_key, display_order)
        )''')
        db.execute('''CREATE INDEX IF NOT EXISTS idx_task_daily_cycle
          ON rp_task_daily_slots (guild_id, character_id, cycle_key)''')


def _deterministic_order(guild_id, character_id, cycle_key, task):
    token = f'{guild_id}:{character_id}:{cycle_key}:{task["id"]}'.encode('utf-8')
    return hashlib.sha256(token).hexdigest()


def get_daily_slots(guild_id, character_id, user_id, *, now=None):
    """建立或讀取這一期的五項任務。重啟、重複開啟均不重新抽取。

    未完成新手任務不派發。若同一期可用任務不足五項，照實顯示。
    """
    if not character_owner(guild_id, character_id, user_id):
        raise ValueError('角色不屬於目前使用者')
    if not is_graduated(guild_id, character_id):
        raise ValueError('請先完成新手任務')
    initialize_daily()
    cycle_key, end_at = current_cycle(now)
    gid, cid = str(guild_id), int(character_id)
    with get_connection() as db:
        existing = db.execute('''SELECT task_id, display_order FROM rp_task_daily_slots
           WHERE guild_id=? AND character_id=? AND cycle_key=? ORDER BY display_order''',
           (gid, cid, cycle_key)).fetchall()
    if not existing:
        actor = character_info(gid, cid, user_id)
        candidates = [t for t in list_tasks(gid, 'daily', now=now.astimezone(timezone.utc).isoformat() if now else None)
                      if eligible(gid, t, actor)['eligible']]
        ordered = sorted(candidates, key=lambda t: _deterministic_order(gid, cid, cycle_key, t))[:MAX_TASKS]
        with transaction() as db:
            # 同時兩個請求也不會覆蓋較早派發的清單。
            already = db.execute('''SELECT 1 FROM rp_task_daily_slots
              WHERE guild_id=? AND character_id=? AND cycle_key=? LIMIT 1''',
              (gid, cid, cycle_key)).fetchone()
            if not already:
                for pos, task in enumerate(ordered, 1):
                    db.execute('''INSERT OR IGNORE INTO rp_task_daily_slots
                       (guild_id,character_id,cycle_key,task_id,display_order)
                       VALUES (?,?,?,?,?)''', (gid, cid, cycle_key, int(task['id']), pos))
    with get_connection() as db:
        rows = db.execute('''SELECT d.*, t.title, t.description, t.rewards_json, t.status
             FROM rp_task_daily_slots AS d
             JOIN rp_task_definitions AS t ON t.id=d.task_id AND t.guild_id=d.guild_id
             WHERE d.guild_id=? AND d.character_id=? AND d.cycle_key=?
             ORDER BY d.display_order''', (gid, cid, cycle_key)).fetchall()
    return {'cycle_key': cycle_key, 'ends_at': end_at, 'tasks': [dict(r) for r in rows], 'available_count':len(rows)}


def daily_task_in_current_cycle(guild_id, character_id, task_id, *, now=None):
    initialize_daily()
    cycle_key, _ = current_cycle(now)
    with get_connection() as db:
        return db.execute('''SELECT 1 FROM rp_task_daily_slots WHERE
          guild_id=? AND character_id=? AND cycle_key=? AND task_id=?''',
          (str(guild_id), int(character_id), cycle_key, int(task_id))).fetchone() is not None
