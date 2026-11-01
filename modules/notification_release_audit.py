"""可供群主管理面板及維運腳本查詢的只讀公告狀況。"""
from __future__ import annotations
from core.database import get_connection
from modules.notification_release_outbox import init_outbox
from modules.notification_update_records import ensure_update_table
from modules.notifications import initialize

def audit_notifications(guild_id: int):
    initialize(); init_outbox(); ensure_update_table()
    gid=str(guild_id)
    with get_connection() as db:
        pending=db.execute("SELECT COUNT(*) n FROM notification_release_outbox WHERE guild_id=? AND state='pending'",(gid,)).fetchone()['n']
        bad=db.execute("SELECT COUNT(*) n FROM notification_release_outbox WHERE guild_id=? AND state='pending' AND last_error IS NOT NULL",(gid,)).fetchone()['n']
        waiting=db.execute("SELECT COUNT(*) n FROM notification_update_items WHERE guild_id=? AND announcement_id IS NULL",(gid,)).fetchone()['n']
        queued=db.execute("SELECT COUNT(*) n FROM notification_outbox WHERE guild_id=? AND dedupe_key LIKE 'bulletin-%' AND status='pending'",(gid,)).fetchone()['n']
        sent=db.execute("SELECT COUNT(*) n FROM notification_announcements WHERE guild_id=? AND message_id IS NOT NULL",(gid,)).fetchone()['n']
    return dict(release_pending=pending, release_errors=bad, updates_waiting=waiting, bulletins_queued=queued, bulletins_delivered=sent)
