"""第五批 13/15：通知公告送達紀錄與核對；不另啟發送器。"""
from __future__ import annotations
from dataclasses import dataclass
from core.database import get_connection, transaction

@dataclass(frozen=True)
class DeliveryReceipt:
    guild_id: int
    announcement_id: int
    message_id: int | None
    outbox_id: int | None
    status: str
    attempts: int
    last_error: str | None


def get_delivery_receipt(guild_id: int, announcement_id: int) -> DeliveryReceipt | None:
    """只讀取本伺服器的公告；由資料庫確認的 message_id 才代表已送達。"""
    with get_connection() as db:
        row = db.execute('''SELECT a.message_id, o.id AS outbox_id, o.status,
            o.attempts, o.last_error FROM notification_announcements a
            LEFT JOIN notification_outbox o
            ON o.guild_id=a.guild_id AND o.dedupe_key=('bulletin-' || a.id)
            WHERE a.guild_id=? AND a.id=?''', (str(guild_id), announcement_id)).fetchone()
    if row is None:
        return None
    return DeliveryReceipt(guild_id, announcement_id,
        int(row['message_id']) if row['message_id'] else None,
        int(row['outbox_id']) if row['outbox_id'] is not None else None,
        row['status'] or 'unqueued', int(row['attempts'] or 0), row['last_error'])


def confirm_delivered(guild_id: int, announcement_id: int, message_id: int) -> bool:
    """僅供成功取得 Discord message.id 的發送器呼叫；禁止預先確認。"""
    if message_id <= 0:
        raise ValueError('message_id 必須為有效 Discord 訊息 ID')
    with transaction() as db:
        row = db.execute('SELECT message_id FROM notification_announcements WHERE guild_id=? AND id=?',
                         (str(guild_id), announcement_id)).fetchone()
        if row is None:
            raise ValueError('找不到本伺服器公告')
        if row['message_id'] and str(row['message_id']) != str(message_id):
            raise ValueError('公告已有不同的送達訊息 ID，需人工核對，避免重複公告')
        db.execute('UPDATE notification_announcements SET message_id=? WHERE guild_id=? AND id=?',
                   (str(message_id), str(guild_id), announcement_id))
        cursor = db.execute('''UPDATE notification_outbox SET status='sent', sent_at=COALESCE(sent_at,CURRENT_TIMESTAMP),
            last_error=NULL, next_attempt_at=NULL
            WHERE guild_id=? AND dedupe_key=? AND status IN ('pending','failed','sent')''',
            (str(guild_id), f'bulletin-{announcement_id}'))
        return cursor.rowcount > 0
