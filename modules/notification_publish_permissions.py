"""第10個：通知公告的群主驗證與發布前檢查。"""
from __future__ import annotations

from core.database import get_connection


def is_guild_owner(interaction) -> bool:
    guild = getattr(interaction, 'guild', None)
    user = getattr(interaction, 'user', None)
    return bool(guild and user and guild.owner_id == user.id)


def check_bulletin_ready(guild_id: int) -> tuple[bool, str]:
    """確認主通知已設置，且有待公告內容；不改動資料。"""
    with get_connection() as db:
        channel = db.execute(
            "SELECT channel_id FROM notification_channels WHERE guild_id=? AND kind='main'",
            (str(guild_id),),
        ).fetchone()
        if not channel:
            return False, '尚未設定主通知頻道，請先到 /通知 設定。'
        count = db.execute(
            'SELECT COUNT(*) FROM notification_update_items WHERE guild_id=? AND announcement_id IS NULL',
            (str(guild_id),),
        ).fetchone()[0]
        if not count:
            return False, '目前沒有尚未公告的正式更新。'
    return True, f'可排入公告：{count} 筆正式更新。'
