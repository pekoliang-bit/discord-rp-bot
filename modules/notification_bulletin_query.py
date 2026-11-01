"""第三批 7/9：安全查詢公告與待發布的內容更新。無 Discord 指令。"""
from __future__ import annotations

from dataclasses import dataclass

from core.database import get_connection
from modules.notification_categories import ordered_active_categories
from modules.notification_update_records import ensure_update_table


@dataclass(frozen=True)
class BulletinSummary:
    announcement_id: int
    guild_id: str
    message_id: str | None
    categories: tuple[str, ...]
    item_count: int


def pending_counts(guild_id: int) -> dict[str, int]:
    """僅統計未納入公告的正式更新，按固定分類順序回傳。"""
    ensure_update_table()
    with get_connection() as db:
        rows = db.execute(
            """SELECT category, COUNT(*) AS n FROM notification_update_items
               WHERE guild_id=? AND announcement_id IS NULL
               GROUP BY category""", (str(guild_id),)
        ).fetchall()
    counts = {row['category']: row['n'] for row in rows}
    return {key: counts[key] for key in ordered_active_categories(counts)}


def read_bulletin(guild_id: int, announcement_id: int) -> BulletinSummary | None:
    """用 guild_id 限制可查範圍，防止跨伺服器讀取公告。"""
    ensure_update_table()
    with get_connection() as db:
        try:
            announcement = db.execute(
                """SELECT id, guild_id, message_id FROM notification_announcements
                   WHERE guild_id=? AND id=?""", (str(guild_id), int(announcement_id))
            ).fetchone()
        except Exception as exc:
            # 尚未完成 notifications.initialize() 時，明確告知初始化缺失。
            import sqlite3
            if isinstance(exc, sqlite3.OperationalError):
                raise RuntimeError('請先執行 modules.notifications.initialize()') from exc
            raise
        if announcement is None:
            return None
        rows = db.execute(
            """SELECT category, COUNT(*) AS n FROM notification_update_items
               WHERE announcement_id=? AND guild_id=? GROUP BY category""",
            (int(announcement_id), str(guild_id))
        ).fetchall()
        counts = {row['category']: row['n'] for row in rows}
        return BulletinSummary(
            announcement_id=int(announcement['id']), guild_id=announcement['guild_id'],
            message_id=announcement['message_id'],
            categories=tuple(ordered_active_categories(counts)), item_count=sum(counts.values())
        )
