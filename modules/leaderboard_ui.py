"""排行榜 28 榜選單與分頁，不另行註冊 /排行。"""
from __future__ import annotations
from datetime import datetime, timezone
import logging

LOG = logging.getLogger(__name__)
import discord
from modules.leaderboard_catalog import BOARDS,BOARD_NAMES
from modules.leaderboard_service import leaderboard

PAGE_SIZE=10
class BoardSelect(discord.ui.Select):
    def __init__(self, current):
        # Discord 單一 Select 最多 25 選項：28 榜分為兩個選單。
        first=BOARDS[:24]
        super().__init__(placeholder='選擇排行榜（1～24）',row=0,
          options=[discord.SelectOption(label=name[:100],value=key,default=key==current)
                   for key,name in first])
    async def callback(self, interaction):
        self.view.board=self.values[0];self.view.page=0
        await self.view.refresh(interaction)
class ExtraSelect(discord.ui.Select):
    def __init__(self,current):
        super().__init__(placeholder='其他排行榜（25～28）',row=1,
          options=[discord.SelectOption(label=name[:100],value=key,default=key==current)
                   for key,name in BOARDS[24:]])
    async def callback(self,interaction):
        self.view.board=self.values[0];self.view.page=0
        await self.view.refresh(interaction)
class LeaderboardView(discord.ui.View):
    def __init__(self,user_id:int,guild_id:int):
        super().__init__(timeout=600)
        self.user_id=user_id;self.guild_id=guild_id
        self.board='appearance';self.page=0
        self.rebuild()
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.user_id or interaction.guild_id!=self.guild_id:
            await interaction.response.send_message('請自行使用 `/排行` 查詢。',ephemeral=True)
            return False
        return True
    def rebuild(self):
        self.clear_items();self.add_item(BoardSelect(self.board));self.add_item(ExtraSelect(self.board))
        self.add_item(self.previous);self.add_item(self.next_page)
        # Activation changes a persisted guild-wide schedule, never a user's payout ledger.
        self.enable_settlement.disabled = False
        self.add_item(self.enable_settlement)
        self.add_item(self.payout_readiness)
    def render(self):
        rows,status=leaderboard(self.guild_id,self.board)
        embed=discord.Embed(title='🏆 排行榜｜'+BOARD_NAMES[self.board],color=discord.Color.gold())
        if status=='unavailable':
            embed.description='此榜已列入 V11 規格，但目前尚未完成可靠的資料來源對接。\n不會顯示推測或虛構排名。'
        elif status=='empty':
            embed.description='目前沒有符合資格的已啟用角色資料。'
        else:
            pages=max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)
            self.page=min(self.page,pages-1)
            visible=rows[self.page*PAGE_SIZE:(self.page+1)*PAGE_SIZE]
            embed.description='\n'.join(f"**#{r['rank']}**｜{discord.utils.escape_markdown(str(r['character_name']))[:65]}｜{r['score']:,}" for r in visible)
            embed.set_footer(text=f'第 {self.page+1}/{pages} 頁｜相同分數並列名次')
        # 排行榜14/30天結算倒數：用每服持久化錨點，不於重啟時重設。
        try:
            from modules.leaderboard_config import get_config
            from modules.leaderboard_cycle import settlement_countdown
            conf=get_config(self.guild_id)
            if conf and conf.get('enabled'):
                self.enable_settlement.disabled = True
                self.enable_settlement.label = '✅ 結算已啟用'
                times=settlement_countdown(conf['anchor_utc'])
                embed.add_field(name='⏳ 排行榜結算倒數', value=(
                    f"小結算（14天）：{times['14d']['remaining']}\n"
                    f"大結算（30天）：{times['30d']['remaining']}"),inline=False)
            else:
                self.enable_settlement.disabled = False
                self.enable_settlement.label = '⏳ 啟用結算'
                embed.add_field(name='⏳ 排行榜結算倒數',value='本伺服器尚未啟用正式排行榜結算，無法推算倒數。',inline=False)
        except Exception:
            embed.add_field(name='⏳ 排行榜結算倒數',value='暫時無法讀取結算設定。',inline=False)
        self.previous.disabled=self.page<=0
        self.next_page.disabled=status!='ready' or (self.page+1)*PAGE_SIZE>=len(rows)
        return embed
    async def refresh(self,interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=self.render(),view=self)
    @discord.ui.button(label='⏳ 啟用結算',style=discord.ButtonStyle.success,row=3)
    async def enable_settlement(self, interaction, button):
        # Guild owner only. Creating an anchor starts future periods; it must not
        # retroactively invent snapshots or enable real-money/attribute payouts.
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message('只有伺服器擁有者能啟用正式排行榜結算。',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            from modules.leaderboard_config import configure, get_config
            from modules.leaderboard_cycle import iso
            old = await __import__('asyncio').to_thread(get_config, self.guild_id)
            anchor = old['anchor_utc'] if old else iso(datetime.now(timezone.utc))
            await __import__('asyncio').to_thread(configure, self.guild_id, anchor, True)
            self.rebuild()
            await interaction.edit_original_response(embed=self.render(),view=self)
            await interaction.followup.send(
                '✅ 已啟用本伺服器 14／30 天排行榜結算與快照封存。'
                '實際獎勵入帳仍由獨立安全開關管制，不會自動發放未核准獎勵。',ephemeral=True)
        except Exception:
            LOG.exception('排行榜啟用失敗 guild=%s', self.guild_id)
            await interaction.followup.send('⚠️ 無法啟用排行榜結算，請查看部署日誌。',ephemeral=True)

    @discord.ui.button(label='🔎 獎勵發放檢查', style=discord.ButtonStyle.secondary, row=3)
    async def payout_readiness(self, interaction, button):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message('只有群主可以查看正式派獎稽核。', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        import asyncio
        try:
            from modules.leaderboard_go_live_audit import audit, summary
            result = await asyncio.to_thread(audit, self.guild_id)
            await interaction.followup.send(summary(result), ephemeral=True)
        except Exception:
            LOG.exception('排行榜派獎稽核失敗 guild=%s', interaction.guild_id)
            await interaction.followup.send('⚠️ 派獎稽核無法執行，詳見 Railway 日誌。', ephemeral=True)

    @discord.ui.button(label='◀️ 上一頁',style=discord.ButtonStyle.secondary,row=2)
    async def previous(self,interaction,button):
        self.page=max(0,self.page-1)
        await self.refresh(interaction)
    @discord.ui.button(label='下一頁 ▶️',style=discord.ButtonStyle.secondary,row=2)
    async def next_page(self,interaction,button):
        self.page+=1
        await self.refresh(interaction)

