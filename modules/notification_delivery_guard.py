"""第11個：公告送出前防止已確認送達的公告再次派送。"""
from __future__ import annotations

from core.database import get_connection, transaction


def bulletin_id_from_key(dedupe_key: str | None) -> int | None:
    if not dedupe_key or not dedupe_key.startswith('bulletin-'):
        return None
    value = dedupe_key[len('bulletin-'):]
    return int(value) if value.isdecimal() and int(value) > 0 else None


def already_delivered(guild_id: int, dedupe_key: str | None) -> bool:
    announcement_id = bulletin_id_from_key(dedupe_key)
    if announcement_id is None:
        return False
    with get_connection() as db:
        row = db.execute(
            'SELECT message_id FROM notification_announcements WHERE id=? AND guild_id=?',
            (announcement_id, str(guild_id)),
        ).fetchone()
        return bool(row and row['message_id'])


def confirm_existing_delivery(outbox_id: int, guild_id: int, dedupe_key: str | None) -> bool:
    """若公告已有 Discord 訊息 ID，將殘留 pending 出站紀錄修正為 sent。"""
    if not already_delivered(guild_id, dedupe_key):
        return False
    with transaction() as db:
        db.execute(
            "UPDATE notification_outbox SET status='sent', sent_at=COALESCE(sent_at,CURRENT_TIMESTAMP) "
            "WHERE id=? AND guild_id=? AND status='pending'",
            (int(outbox_id), str(guild_id)),
        )
    return True
