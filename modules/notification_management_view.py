"""第20個檔案：可嵌入既有 /通知 的公告管理介面，不新增斜線指令。"""
from __future__ import annotations

import discord

from modules.notification_management_status import bulletin_management_status
from modules.notification_bulletin_history_view import BulletinHistoryView, history_content
from modules.notification_retry_buttons import FailedBulletinRetryView, failed_bulletin_content


def _authorized(interaction: discord.Interaction, guild_id: int) -> bool:
    return bool(interaction.guild and interaction.guild.id == guild_id
                and interaction.user.id == interaction.guild.owner_id)


class NotificationManagementView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = int(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _authorized(interaction, self.guild_id):
            return True
        await interaction.response.send_message('只有本伺服器群主可以查看公告管理。', ephemeral=True)
        return False

    @discord.ui.button(label='最近公告紀錄', emoji='📋', style=discord.ButtonStyle.secondary)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            history_content(self.guild_id), view=BulletinHistoryView(self.guild_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='失敗公告補發', emoji='🔁', style=discord.ButtonStyle.secondary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            failed_bulletin_content(self.guild_id), view=FailedBulletinRetryView(self.guild_id),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='重新整理', emoji='🔄', style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=bulletin_management_status(self.guild_id),
                                                view=NotificationManagementView(self.guild_id),
                                                allowed_mentions=discord.AllowedMentions.none())
