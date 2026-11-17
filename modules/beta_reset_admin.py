"""官方 → 功能管理 → 測試角重抽重置。只認群主 / Discord Administrator。"""
from __future__ import annotations
import asyncio
import discord
from core import beta_test_core as beta


def _is_admin(i):
    return (i.guild is not None and
            (i.user.id == i.guild.owner_id or
             bool(getattr(i.user.guild_permissions,'administrator',False))))


class BetaResetSelect(discord.ui.Select):
    def __init__(self, rows):
        self.users={str(r['owner_id']) for r in rows}
        options=[discord.SelectOption(
            label=str(r['name'])[:45],
            value=str(r['owner_id']),
            description=(f"玩家 {r['owner_id']}｜角色 #{r['character_id']}")[:100]
        ) for r in rows[:20]]
        super().__init__(placeholder='勾選要重新抽取的成員（可複選）',
                         min_values=1,max_values=len(options),options=options,row=0)

    async def callback(self,i):
        if not _is_admin(i) or i.user.id != self.view.actor_id:
            return await i.response.send_message('⛔ 無操作權限。',ephemeral=True)
        chosen=[uid for uid in self.values if uid in self.users]
        if not chosen:
            return await i.response.send_message('沒有選取有效成員。',ephemeral=True)
        await i.response.send_message(
            f'⚠️ 確定重置 {len(chosen)} 位成員的測試角嗎？\n'
            '舊測試角會標記刪除，原本抽中的姓名永遠不再進抽獎池；'
            '角色論壇和歷史訊息不會自動刪除。重置後成員可從 /創角 重新抽取。',
            view=ConfirmBetaReset(i.user.id,chosen),ephemeral=True)


class BetaResetView(discord.ui.View):
    def __init__(self, actor_id, rows):
        super().__init__(timeout=180)
        self.actor_id=int(actor_id)
        self.add_item(BetaResetSelect(rows))


class ConfirmBetaReset(discord.ui.View):
    def __init__(self,actor_id,owner_ids):
        super().__init__(timeout=120)
        self.actor_id=int(actor_id)
        self.owner_ids=list(owner_ids)

    @discord.ui.button(label='確定重置所選測試角',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        if not _is_admin(i) or i.user.id != self.actor_id:
            return await i.response.send_message('⛔ 無操作權限。',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            n=await asyncio.to_thread(beta.reset_claims,i.guild_id,i.user.id,self.owner_ids)
            for item in self.children: item.disabled=True
            await i.followup.send(f'✅ 已重置 {n} 位成員。請他們重新進入 /創角 抽取新測試角。\n'
                '舊名字仍算已使用，沒有重新放回抽獎池。',ephemeral=True)
        except Exception as exc:
            await i.followup.send(f'⚠️ 重置未完成：{exc}',ephemeral=True)


class BetaResetButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🎲 測試角重抽重置',style=discord.ButtonStyle.secondary,row=2)

    async def callback(self,i):
        if not _is_admin(i):
            return await i.response.send_message('⛔ 僅群主或 Administrator 可使用。',ephemeral=True)
        rows=await asyncio.to_thread(beta.resettable_players,i.guild_id)
        if not rows:
            return await i.response.send_message('目前沒有可重抽的測試角色。',ephemeral=True)
        await i.response.send_message(
            f'🎲 **測試角色重抽管理**\n目前 {len(rows)} 位成員有測試角色。'
            '以下顯示前20位；選取後仍需二次確認。\n'
            '已抽過的名字不會因重置而釋出。',
            view=BetaResetView(i.user.id,rows[:20]),ephemeral=True)
