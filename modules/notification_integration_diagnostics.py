"""第25檔：唯讀通知系統整合診斷，不新增 Discord 斜線指令。"""
from __future__ import annotations
from core.database import get_connection

CHECK_TABLES = ('notification_update_items', 'notification_release_outbox')


def inspect_guild_updates(guild_id: int) -> dict:
    result = {'guild_id': str(guild_id), 'tables': {}, 'counts': {}, 'warnings': []}
    with get_connection() as db:
        existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in CHECK_TABLES:
            result['tables'][name] = name in existing
        if 'notification_update_items' in existing:
            result['counts']['unannounced'] = db.execute('''SELECT COUNT(*) FROM notification_update_items
               WHERE guild_id=? AND announcement_id IS NULL''', (str(guild_id),)).fetchone()[0]
            result['counts']['missing_source'] = db.execute('''SELECT COUNT(*) FROM notification_update_items
               WHERE guild_id=? AND (source_url IS NULL OR source_url='')''', (str(guild_id),)).fetchone()[0]
        if 'notification_release_outbox' in existing:
            result['counts']['pending_outbox'] = db.execute('''SELECT COUNT(*) FROM notification_release_outbox
               WHERE guild_id=? AND state='pending' ''', (str(guild_id),)).fetchone()[0]
            result['counts']['failed_outbox'] = db.execute('''SELECT COUNT(*) FROM notification_release_outbox
               WHERE guild_id=? AND state='pending' AND last_error IS NOT NULL''', (str(guild_id),)).fetchone()[0]
    if not all(result['tables'].values()):
        result['warnings'].append('部分資料表尚未初始化；請先執行相應初始化程序')
    if result['counts'].get('missing_source', 0):
        result['warnings'].append('有更新缺少原始連結，B 方案詳細頁將無法直接跳轉')
    return result
