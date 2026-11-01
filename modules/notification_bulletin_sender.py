"""第三批 8/9：可由現有通知派送器呼叫的公告傳送函式。

本模組不啟動另一個排程器，亦不會自行重複消費 outbox。
傳送成功後回傳 Discord 訊息 ID；資料庫確認應由呼叫端處理。
"""
from __future__ import annotations

import discord
from core.database import get_connection
from modules.notification_categories import ordered_active_categories
from modules.notification_detail_buttons import AnnouncementDetailView


def announcement_categories(guild_id: int, announcement_id: int) -> list[str]:
    """讀取該公告有實際詳細資料的分類；不混入其他伺服器公告。"""
    with get_connection() as db:
        exists = db.execute(
            'SELECT 1 FROM notification_announcements WHERE id=? AND guild_id=?',
            (int(announcement_id), str(guild_id))
        ).fetchone()
        if not exists:
            raise ValueError('查無這個伺服器的公告')
        categories = {r['category'] for r in db.execute(
            '''SELECT DISTINCT category FROM notification_update_items
               WHERE announcement_id=? AND guild_id=?''',
            (int(announcement_id), str(guild_id))
        )}
    return ordered_active_categories(categories)


async def send_bulletin(channel, guild_id: int, announcement_id: int, body: str) -> discord.Message:
    """僅發送 Discord 公告，不更改 outbox 狀態；避免成功與失敗誤判。"""
    if not getattr(channel, 'guild', None) or channel.guild.id != int(guild_id):
        raise ValueError('公告頻道不屬於指定伺服器')
    if len(body) > 2000:
        raise ValueError('Discord 公告長度不能超過 2000 字元')
    categories = announcement_categories(guild_id, announcement_id)
    view = AnnouncementDetailView(announcement_id, categories) if categories else None
    return await channel.send(
        body, view=view, allowed_mentions=discord.AllowedMentions.none()
    )
