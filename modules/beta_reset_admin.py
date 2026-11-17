"""官方 → 功能管理 → 測試姓名池整池重置（需二次確認）。"""
from __future__ import annotations

import asyncio
import discord
from core import beta_test_core as beta


def _is_admin(i):
    return (i.guild is not None and
            (i.user.id == i.guild.owner_id or
             bool(getattr(i.user.guild_permissions, 'administrator', False))))


class ConfirmPoolReset(discord.ui.View):
    def __init__(self, actor_id: int):
        super().__init__(timeout=120)
        self.actor_id = int(actor_id)
        self.completed = False

    @discord.ui.button(label='確認清除舊抽角資料並重置34名', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_admin(interaction) or interaction.user.id != self.actor_id:
            return await interaction.response.send_message('⛔ 無操作權限。', ephemeral=True)
        if self.completed:
            return await interaction.response.send_message('此操作已經執行，請勿重複點擊。', ephemeral=True)
        self.completed = True
        await interaction.response.defer(ephemeral=True)
        try:
            n, count = await asyncio.to_thread(
                beta.reset_entire_pool, interaction.guild_id, interaction.user.id
            )
        except Exception as exc:
            self.completed = False
            return await interaction.followup.send(f'⚠️ 整池重置失敗：{exc}', ephemeral=True)
        for component in self.children:
            component.disabled = True
        await interaction.followup.send(
            f'✅ 已重置整個測試抽角池！停用 {n} 個本輪測試角，'
            f'目前恢復 {count} 個可抽姓名（男性17、女性17）。\n'
            '請玩家重新使用 /創角 → 領取測試角。每抽一名，就從本輪剩餘名單移除。\n'
            '舊抽角歷史已清除，測試抽角序號重新從1開始；Discord舊論壇頻道需另行清理。', ephemeral=True
        )

    @discord.ui.button(label='取消，不重置', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message('⛔ 只能由原操作人取消。', ephemeral=True)
        for component in self.children:
            component.disabled = True
        await interaction.response.edit_message(content='已取消整池重置。', view=self)


class BetaResetButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🎲 測試姓名池整池重置', style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            return await interaction.response.send_message('⛔ 僅群主或 Administrator 可使用。', ephemeral=True)
        if await asyncio.to_thread(beta.beta_ended, interaction.guild_id):
            return await interaction.response.send_message('內測已結束，不能重置姓名池。', ephemeral=True)
        rows = await asyncio.to_thread(beta.resettable_players, interaction.guild_id)
        remaining = await asyncio.to_thread(beta.remaining_names, interaction.guild_id)
        await interaction.response.send_message(
            '⚠️ **測試姓名池整池重置**\n'
            f'目前本輪有 {len(rows)} 位仍持有測試角色，姓名池剩餘 {len(remaining)}/34。\n'
            '確認後將：\n'
            '① 停用並清除本服所有歷史測試抽角資料（舊角色僅保留不可用的資料庫占位）。\n'
            '② 清空本輪姓名占用，恢復最初全部34個名字。\n'
            '③ 測試抽角序號重新從1開始；新一輪抽到名字後立刻移出可抽池。\n'
            '**這會影響所有已領測試角的成員，不只是選定的兩位。**',
            view=ConfirmPoolReset(interaction.user.id), ephemeral=True
        )
