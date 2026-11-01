"""第19個檔案：/通知 公告管理狀態文字（唯讀，不建立指令）。"""
from __future__ import annotations

from core.database import get_connection


def bulletin_management_status(guild_id: int) -> str:
    """查詢本伺服器待公告、發送中、送達及失敗筆數。"""
    gid = str(int(guild_id))
    with get_connection() as db:
        pending = db.execute(
            'SELECT COUNT(*) FROM notification_update_items WHERE guild_id=? AND announcement_id IS NULL',
            (gid,),
        ).fetchone()[0]
        counts = {r['status']: int(r['n']) for r in db.execute(
            """SELECT o.status, COUNT(*) AS n FROM notification_outbox o
               WHERE o.guild_id=? AND o.dedupe_key LIKE 'bulletin-%'
               GROUP BY o.status""", (gid,)
        ).fetchall()}
        received = db.execute(
            'SELECT COUNT(*) FROM notification_announcements WHERE guild_id=? AND message_id IS NOT NULL',
            (gid,),
        ).fetchone()[0]
    return ('📋 **公告管理**\n'
            f'待公告更新：{pending} 筆\n'
            f'排隊中：{counts.get("pending", 0)} 則｜發送失敗：{counts.get("failed", 0)} 則\n'
            f'確認送達：{received} 則')
