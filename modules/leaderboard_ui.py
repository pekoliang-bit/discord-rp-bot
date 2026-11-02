"""排行榜 28 榜選單與分頁，不另行註冊 /排行。"""
from __future__ import annotations
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
        self.previous.disabled=self.page<=0
        self.next_page.disabled=status!='ready' or (self.page+1)*PAGE_SIZE>=len(rows)
        return embed
    async def refresh(self,interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=self.render(),view=self)
    @discord.ui.button(label='◀️ 上一頁',style=discord.ButtonStyle.secondary,row=2)
    async def previous(self,interaction,button):
        self.page=max(0,self.page-1)
        await self.refresh(interaction)
    @discord.ui.button(label='下一頁 ▶️',style=discord.ButtonStyle.secondary,row=2)
    async def next_page(self,interaction,button):
        self.page+=1
        await self.refresh(interaction)
