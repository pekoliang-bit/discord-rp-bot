"""第三批 9/9：通知入口整合橋接層（需由現有 notifications.py 顯式呼叫）。

不設 setup()、不註冊 /通知、不啟動背景 task，避免重複指令與派送。
使用前先呼叫 modules.notifications.initialize()。
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.notification_bulletin_query import pending_counts
from modules.notification_bulletin_service import prepare_bulletin
from modules.notification_detail_buttons import restore_announcement_buttons


@dataclass(frozen=True)
class BulletinEntryStatus:
    pending_by_category: dict[str, int]
    total_pending: int


def bulletin_entry_status(guild_id: int) -> BulletinEntryStatus:
    """供 /通知 主面板顯示本期尚未公告的更新數量。"""
    counts = pending_counts(guild_id)
    return BulletinEntryStatus(pending_by_category=counts, total_pending=sum(counts.values()))


def queue_bulletin_now(guild_id: int) -> int | None:
    """手動觸發準備公告。僅排入 outbox，並非已送達 Discord。

    群主權限檢查必須在現有 Discord 指令處理器進行。
    實際送達由現有 Notifications.dispatcher 負責。
    """
    return prepare_bulletin(guild_id, include_legacy=False)


def restore_buttons(bot) -> int:
    """啟動時由現有通知 Cog 呼叫一次，恢復第二批公告按鈕。"""
    return restore_announcement_buttons(bot)
