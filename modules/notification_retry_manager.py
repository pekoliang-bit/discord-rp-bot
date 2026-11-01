"""第五批 14/15：失敗公告人工補發佇列管理；由既有通知排程器發送。"""
from __future__ import annotations
from dataclasses import dataclass
from core.database import get_connection, transaction
from modules.notification_delivery_receipts import get_delivery_receipt

@dataclass(frozen=True)
class FailedBulletin:
    announcement_id: int
    outbox_id: int
    attempts: int
    error: str | None


def list_failed_bulletins(guild_id: int, limit: int = 20) -> list[FailedBulletin]:
    with get_connection() as db:
        rows = db.execute('''SELECT a.id AS aid,o.id AS oid,o.attempts,o.last_error
            FROM notification_announcements a JOIN notification_outbox o
              ON o.guild_id=a.guild_id AND o.dedupe_key=('bulletin-'||a.id)
            WHERE a.guild_id=? AND a.message_id IS NULL AND o.status='failed'
            ORDER BY o.id DESC LIMIT ?''', (str(guild_id), max(1,min(limit,100)))).fetchall()
    return [FailedBulletin(int(r['aid']),int(r['oid']),int(r['attempts']),r['last_error']) for r in rows]


def requeue_failed_bulletin(guild_id: int, announcement_id: int, *, owner_authorized: bool = False) -> bool:
    """只允許通過外層群主驗證的管理流程呼叫；不直接發 Discord 訊息。"""
    if not owner_authorized:
        raise PermissionError('只有群主能手動補發公告')
    receipt = get_delivery_receipt(guild_id, announcement_id)
    if receipt is None:
        raise ValueError('查無本伺服器的公告')
    if receipt.message_id is not None:
        return False  # 已送達，不允許重新排隊
    with transaction() as db:
        cur = db.execute('''UPDATE notification_outbox SET status='pending', attempts=0,
            next_attempt_at=NULL,last_error=NULL
            WHERE guild_id=? AND dedupe_key=? AND status='failed'
            AND EXISTS (SELECT 1 FROM notification_announcements a
                        WHERE a.id=? AND a.guild_id=? AND a.message_id IS NULL)''',
            (str(guild_id), f'bulletin-{announcement_id}',announcement_id,str(guild_id)))
        return cur.rowcount == 1
