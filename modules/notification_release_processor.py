"""將已公開內容的 outbox 轉入公告記錄；僅一個排程器使用。"""
from __future__ import annotations
import logging
from core.database import transaction
from modules.notification_release_outbox import init_outbox
from modules.notification_update_records import ensure_update_table
from modules.notification_categories import LABELS, ACTIONS
from modules.notification_story_publication import parse_discord_source

LOG = logging.getLogger(__name__)

def drain_published_releases(*, limit=100):
    """在同一交易內原子式轉存與確認，避免中途崩潰造成重複公告。"""
    init_outbox()
    ensure_update_table()
    counts = {'recorded': 0, 'duplicate': 0, 'failed': 0}
    with transaction() as db:
        items = list(db.execute("SELECT * FROM notification_release_outbox WHERE state='pending' ORDER BY id LIMIT ?", (max(1,min(int(limit),500)),)))
        for row in items:
            try:
                if row['category'] not in LABELS or row['action'] not in ACTIONS:
                    raise ValueError('不支援的分類或操作')
                if not str(row['title'] or '').strip():
                    raise ValueError('缺少標題')
                if not row['source_url']:
                    raise ValueError('正式更新必須有原始內容連結')
                source = parse_discord_source(row['source_url'], int(row['guild_id']))
                cursor = db.execute("""INSERT OR IGNORE INTO notification_update_items
                    (guild_id,category,title,action,source_url,character_name,update_key)
                    VALUES(?,?,?,?,?,?,?)""", (row['guild_id'],row['category'],row['title'][:180],
                    row['action'],source,row['character_name'],row['release_key']))
                db.execute("UPDATE notification_release_outbox SET state='recorded',last_error=NULL WHERE id=?",(row['id'],))
                counts['recorded' if cursor.rowcount else 'duplicate'] += 1
            except (ValueError, TypeError) as exc:
                db.execute("UPDATE notification_release_outbox SET last_error=? WHERE id=?",(str(exc)[:300],row['id']))
                counts['failed'] += 1
    return counts
