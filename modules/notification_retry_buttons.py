"""第六批 17/18：失敗公告人工補發按鈕；不自行發訊或啟動排程。"""
from __future__ import annotations

import discord
from modules.notification_retry_manager import list_failed_bulletins, requeue_failed_bulletin


def failed_bulletin_content(guild_id: int) -> str:
    rows = list_failed_bulletins(guild_id)
    if not rows:
        return '✅ 目前沒有可人工補發的失敗公告。'
    lines = ['⚠️ **發送失敗的公告**']
    for r in rows:
        # 不把內部錯誤細節顯示到公開頻道
        lines.append(f'• 公告 #{r.announcement_id}｜嘗試 {r.attempts} 次｜等待人工補發')
    return '\n'.join(lines)[:1800]


class FailedBulletinRetryView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = int(guild_id)
        rows = list_failed_bulletins(guild_id)
        if rows:
            select = discord.ui.Select(placeholder='選擇需要重新排隊的公告',
                options=[discord.SelectOption(label=f'補發公告 #{r.announcement_id}',
                           value=str(r.announcement_id)) for r in rows[:20]])
            select.callback = self._retry
            self.add_item(select)

    async def _retry(self, interaction: discord.Interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message('只有本伺服器群主能重新排隊。', ephemeral=True)
            return
        aid = int(self.children[0].values[0])
        try:
            done = requeue_failed_bulletin(self.guild_id, aid, owner_authorized=True)
        except (ValueError, PermissionError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not done:
            await interaction.response.send_message('公告已送達、狀態改變，或不符合補發條件。', ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ 公告 #{aid} 已重新排入既有通知佇列；尚未確認送達。', ephemeral=True)
