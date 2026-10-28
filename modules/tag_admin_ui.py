"""群主標籤庫管理介面。"""
import discord
from discord.ext import commands
from core.database import transaction,get_connection
from modules.tag_library import tags,seed,held

def owner(i):return bool(i.guild and i.guild.owner_id==i.user.id)
class AddTags(discord.ui.Modal,title='批次新增標籤'):
    category=discord.ui.TextInput(label='內部分類',default='其他',max_length=40)
    lines=discord.ui.TextInput(label='每行一個標籤',style=discord.TextStyle.paragraph,max_length=3500)
    async def on_submit(self,i):
        if not owner(i):return await i.response.send_message('僅限群主。',ephemeral=True)
        names=list(dict.fromkeys(x.strip() for x in str(self.lines).splitlines() if x.strip()))
        if not names or any(len(x)>40 for x in names):return await i.response.send_message('請填標籤，每個不超過40字。',ephemeral=True)
        with transaction() as db:
            for name in names:db.execute('INSERT OR IGNORE INTO rp_tag_library(guild_id,name,category) VALUES(?,?,?)',(str(i.guild_id),name,str(self.category)))
        await i.response.send_message(f'✅ 已處理 {len(names)} 個標籤。',ephemeral=True)
class EditTag(discord.ui.Modal,title='修改標籤'):
    old=discord.ui.TextInput(label='既有標籤名稱',max_length=40)
    new=discord.ui.TextInput(label='新名稱（停用時可留空）',required=False,max_length=40)
    action=discord.ui.TextInput(label='操作：更名／啟用／停用',max_length=2)
    async def on_submit(self,i):
        if not owner(i):return await i.response.send_message('僅限群主。',ephemeral=True)
        action=str(self.action).strip();old=str(self.old).strip();new=str(self.new).strip()
        if action not in ('更名','啟用','停用') or (action=='更名' and not new):return await i.response.send_message('請正確填寫操作及名稱。',ephemeral=True)
        try:
            with transaction() as db:
                if action=='更名':cur=db.execute('UPDATE rp_tag_library SET name=? WHERE guild_id=? AND name=?',(new,str(i.guild_id),old))
                else:cur=db.execute('UPDATE rp_tag_library SET enabled=? WHERE guild_id=? AND name=?',(1 if action=='啟用' else 0,str(i.guild_id),old))
        except Exception as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.send_message('✅ 已更新。' if cur.rowcount else '找不到標籤。',ephemeral=True)
class TagAdminView(discord.ui.View):
    def __init__(self,i):super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id;seed(i.guild_id)
    async def interaction_check(self,i):
        if not owner(i) or i.user.id!=self.uid or i.guild_id!=self.gid:
            await i.response.send_message('沒有權限。',ephemeral=True);return False
        return True
    @discord.ui.button(label='新增標籤',style=discord.ButtonStyle.primary)
    async def add(self,i,b):await i.response.send_modal(AddTags())
    @discord.ui.button(label='標籤清單',style=discord.ButtonStyle.secondary)
    async def listing(self,i,b):
        data=tags(i.guild_id,False)
        lines=[f"{x['name']}（{x['category']}）{'啟用' if x['enabled'] else '停用'}" for x in data]
        text='\n'.join(lines)
        for start in range(0,len(text),1800):
            if start==0:await i.response.send_message(text[:1800] or '目前無標籤',ephemeral=True)
            else:await i.followup.send(text[start:start+1800],ephemeral=True)
    @discord.ui.button(label='更名／啟停',style=discord.ButtonStyle.secondary)
    async def edit(self,i,b):await i.response.send_modal(EditTag())
    @discord.ui.button(label='返回內容建立',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        from modules.content_builder import ContentView
        await i.response.edit_message(content='✍️ 內容建立中心',view=ContentView(i))
async def setup(bot:commands.Bot):pass
