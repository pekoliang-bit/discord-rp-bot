"""正式內容更新的紀錄框架：使用既有通知資料表，不碰玩家資料。"""
from __future__ import annotations

import re
from core.database import transaction
from modules.notification_categories import ACTIONS, LABELS

DISCORD_LINK = re.compile(
    r"^https://(?:discord\.com|(?:www\.)?discordapp\.com)/channels/\d+/\d+(?:/\d+)?/?(?:\?.*)?$",
    re.I,
)


def ensure_update_table() -> None:
    """可重複執行，兼容第二階段通知模組的 notification_update_items。"""
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS notification_update_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            category TEXT NOT NULL, title TEXT NOT NULL, action TEXT NOT NULL,
            source_url TEXT, character_name TEXT, update_key TEXT,
            announcement_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,update_key))''')
        db.execute('''CREATE INDEX IF NOT EXISTS idx_notification_updates
            ON notification_update_items(guild_id,announcement_id,category,id)''')


def record_published_update(
    guild_id: int, category: str, title: str, *, action: str = "新增",
    source_url: str | None = None, character_name: str | None = None,
    update_key: str | None = None, approved: bool = False,
) -> bool:
    """由正式發布/完成審核流程呼叫；草稿不可入列。

    update_key 建議使用例如 story:角色ID:劇情ID:revision 的穩定鍵，避免重覆計數。
    回傳是否成功新增；相同 update_key 不重覆寫入。
    """
    if not approved:
        raise ValueError("只有已審核或已正式公開的更新可以登錄")
    if category not in LABELS or action not in ACTIONS:
        raise ValueError("未知分類或更新動作")
    if not title or not title.strip():
        raise ValueError("更新標題不可為空")
    if source_url is not None and not DISCORD_LINK.fullmatch(source_url):
        raise ValueError("來源必須是 Discord 頻道、帖子或訊息連結")
    ensure_update_table()
    with transaction() as db:
        cursor = db.execute('''INSERT OR IGNORE INTO notification_update_items
            (guild_id, category, title, action, source_url, character_name, update_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (str(guild_id), category, title.strip()[:180], action,
             source_url, (character_name or "").strip()[:80] or None, update_key))
        return cursor.rowcount > 0
