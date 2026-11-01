"""第二批 1/3：公告分類按鈕（Discord UI，僅顯示該公告的資料）。

使用方式：view = AnnouncementDetailView(announcement_id, active_categories)
此檔案沒有 setup()，不會新增斜線指令。
"""
from __future__ import annotations

import discord
from core.database import get_connection
from modules.notification_categories import CATEGORIES, LABELS
from modules.notification_announcement_format import format_category_details


class AnnouncementDetailView(discord.ui.View):
    def __init__(self, announcement_id: int, active_categories: list[str]):
        super().__init__(timeout=None)
        self.announcement_id = int(announcement_id)
        known = [key for key, _ in CATEGORIES if key in set(active_categories)]
        # Discord 每則訊息最多 25 個按鈕；本專案僅九類。
        for category in known:
            button = discord.ui.Button(
                label=LABELS[category], style=discord.ButtonStyle.secondary,
                custom_id=f"rp:bulletin:{self.announcement_id}:{category}",
            )
            button.callback = self._callback(category)
            self.add_item(button)

    def _callback(self, category: str):
        async def callback(interaction: discord.Interaction):
            if not interaction.guild_id:
                await interaction.response.send_message("只能在伺服器使用。", ephemeral=True)
                return
            with get_connection() as db:
                owner = db.execute(
                    "SELECT guild_id FROM notification_announcements WHERE id=?",
                    (self.announcement_id,),
                ).fetchone()
                if owner is None or owner["guild_id"] != str(interaction.guild_id):
                    await interaction.response.send_message("找不到此伺服器的公告。", ephemeral=True)
                    return
                rows = db.execute(
                    """SELECT category, action, title, character_name, source_url
                       FROM notification_update_items
                       WHERE announcement_id=? AND category=? ORDER BY id""",
                    (self.announcement_id, category),
                ).fetchall()
            pages = format_category_details(rows, category)
            await interaction.response.send_message(
                pages[0], ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
            for page in pages[1:]:
                await interaction.followup.send(
                    page, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                )
        return callback


def restore_announcement_buttons(bot: discord.Client) -> int:
    """啟動時由現有 Notifications Cog 呼叫，恢復已送出的公告按鈕。"""
    count = 0
    with get_connection() as db:
        announcements = db.execute(
            "SELECT id, message_id FROM notification_announcements WHERE message_id IS NOT NULL"
        ).fetchall()
        for item in announcements:
            categories = [r["category"] for r in db.execute(
                """SELECT DISTINCT category FROM notification_update_items
                   WHERE announcement_id=?""", (item["id"],)
            )]
            if categories:
                bot.add_view(AnnouncementDetailView(item["id"], categories), message_id=int(item["message_id"]))
                count += 1
    return count
