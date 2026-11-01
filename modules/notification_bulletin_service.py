"""第二批 2/3：手動/排程公告的資料庫發佈準備。

先以一筆交易鎖定待公告項目並放進既有 notification_outbox。
真正傳送依賴現有 modules.notifications.Notifications.dispatcher。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.database import transaction
from modules.notification_announcement_format import format_announcement
from modules.notification_categories import LEGACY_CATEGORY


def prepare_bulletin(guild_id: int, *, include_legacy: bool = False) -> int | None:
    """將待公告內容彙整、登記並排入通知佇列；回傳公告 ID。

    include_legacy=False 時只處理有詳細連結的新版更新紀錄。
    沒有待公告項目就不建立空公告。需事先執行 notifications.initialize()。
    """
    gid = str(guild_id)
    with transaction() as db:
        rows = db.execute(
            """SELECT * FROM notification_update_items
               WHERE guild_id=? AND announcement_id IS NULL ORDER BY id""", (gid,)
        ).fetchall()
        legacy = db.execute(
            "SELECT * FROM notification_digest WHERE guild_id=?", (gid,)
        ).fetchall() if include_legacy else []
        if not rows and not legacy:
            return None
        body, active = format_announcement(rows, date=datetime.now(ZoneInfo("Asia/Taipei")))
        if legacy:
            # 舊事件沒有來源連結：只在摘要附上數量，避免假裝可點進原始內容。
            counts: dict[str, int] = {}
            for row in legacy:
                key = LEGACY_CATEGORY.get(row["event_type"], "other")
                counts[key] = counts.get(key, 0) + row["amount"]
            body += "\n\n舊版事件彙總（無詳細連結）：" + "、".join(
                f"{key} {count} 項" for key, count in counts.items()
            )
            body = body[:1950]
        cursor = db.execute("INSERT INTO notification_announcements(guild_id) VALUES (?)", (gid,))
        announcement_id = cursor.lastrowid
        db.execute(
            """UPDATE notification_update_items SET announcement_id=?
               WHERE guild_id=? AND announcement_id IS NULL""", (announcement_id, gid)
        )
        if legacy:
            for row in legacy:
                db.execute(
                    """INSERT INTO notification_announcement_legacy
                       (announcement_id,category,action,amount) VALUES (?,?,?,?)""",
                    (announcement_id, LEGACY_CATEGORY.get(row["event_type"], "other"),
                     row["action"], row["amount"]),
                )
            db.execute("DELETE FROM notification_digest WHERE guild_id=?", (gid,))
        db.execute(
            """INSERT OR IGNORE INTO notification_outbox
               (guild_id,kind,event_type,body,dedupe_key)
               VALUES (?,?,?,?,?)""",
            (gid, "main", "content", body, f"bulletin-{announcement_id}"),
        )
        return announcement_id
