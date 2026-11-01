"""第23檔：正式公開事件暫存匣，避免業務流程的提交與公告登錄混淆。

業務系統應在自己的交易提交成功後 enqueue_release，再由 process_pending 處理。
本模組不自行排程，不會影響現有通知派送器。
"""
from __future__ import annotations
import json
from core.database import transaction, get_connection
from modules.notification_update_records import record_published_update


def init_outbox():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS notification_release_outbox (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
          release_key TEXT NOT NULL, category TEXT NOT NULL,
          title TEXT NOT NULL, action TEXT NOT NULL,
          source_url TEXT, character_name TEXT,
          state TEXT NOT NULL DEFAULT 'pending',
          last_error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(guild_id,release_key))''')


def enqueue_release(guild_id, *, release_key, category, title, source_url,
                    character_name=None, action='新增', published=False):
    if not published:
        raise ValueError('只有正式公開內容可以登錄')
    if not release_key or not str(release_key).strip():
        raise ValueError('release_key 不可空白')
    init_outbox()
    with transaction() as db:
        c = db.execute('''INSERT OR IGNORE INTO notification_release_outbox
          (guild_id,release_key,category,title,action,source_url,character_name)
          VALUES(?,?,?,?,?,?,?)''', (str(guild_id), str(release_key), category,
          title, action, source_url, character_name))
        return c.rowcount == 1


def process_pending(guild_id, *, limit=50):
    """由既有排程或管理操作呼叫；失敗項目留在 pending 待修復。"""
    init_outbox()
    with get_connection() as db:
        rows = [dict(r) for r in db.execute('''SELECT * FROM notification_release_outbox
          WHERE guild_id=? AND state='pending' ORDER BY id LIMIT ?''',
          (str(guild_id), max(1,min(int(limit),100))))]
    sent = failed = 0
    for row in rows:
        try:
            record_published_update(guild_id, row['category'], row['title'],
                action=row['action'], source_url=row['source_url'],
                character_name=row['character_name'],
                update_key=row['release_key'], approved=True)
            with transaction() as db:
                db.execute("UPDATE notification_release_outbox SET state='recorded', last_error=NULL WHERE id=? AND guild_id=?", (row['id'], str(guild_id)))
            sent += 1
        except Exception as exc:
            with transaction() as db:
                db.execute('UPDATE notification_release_outbox SET last_error=? WHERE id=? AND guild_id=?', (str(exc)[:300], row['id'], str(guild_id)))
            failed += 1
    return {'recorded':sent,'failed':failed}
