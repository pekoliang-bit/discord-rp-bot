"""玩家初始標籤：可分頁搜尋、限定角色擁有者。"""
import logging
import discord
from discord.ext import commands
from modules.tag_library import tags, held, replace_initial, initial_limit, seed

LOG = logging.getLogger(__name__)

class TagSearch(discord.ui.Modal,title='搜尋標籤庫'):
    word=discord.ui.TextInput(label='標籤關鍵字（留空顯示全部）',required=False,max_length=40)
    def __init__(self,view): super().__init__();self.target=view
    async def on_submit(self,i):
        self.target.keyword=str(self.word.value).strip();self.target.page=0
        self.target.refresh()
        await i.response.send_message(content=self.target.caption(),view=self.target,ephemeral=True)

class TagPick(discord.ui.Select):
    def __init__(self,parent):
        pool=parent.pool();page=pool[parent.page*25:(parent.page+1)*25]
        opts=[discord.SelectOption(label=r['name'],value=r['name'],default=r['name'] in parent.selected) for r in page]
        if not opts: opts=[discord.SelectOption(label='沒有相符標籤',value='__empty__')]
        super().__init__(placeholder='選擇本頁標籤（可複選）',min_values=0,max_values=max(1,min(parent.limit,len(page))),options=opts,disabled=not bool(page),row=0)
        self.picker_view=parent
    async def callback(self,i):
        page_names={r['name'] for r in self.picker_view.pool()[self.picker_view.page*25:(self.picker_view.page+1)*25]}
        kept=[x for x in self.picker_view.selected if x not in page_names]
        chosen=[x for x in self.values if x!='__empty__']
        if len(kept)+len(chosen)>self.picker_view.limit:
            return await i.response.send_message(f'最多選 {self.picker_view.limit} 個，請取消其他頁的選擇。',ephemeral=True)
        self.picker_view.selected=kept+chosen
        self.picker_view.refresh()
        await i.response.edit_message(content=self.picker_view.caption(),view=self.parent)

class TagSelectionView(discord.ui.View):
    def __init__(self,gid,cid,owner_id,actor_id,kind,*,force=False,notice=''):
        super().__init__(timeout=600)
        seed(gid)
        self.gid=int(gid);self.cid=int(cid);self.owner_id=int(owner_id);self.actor_id=int(actor_id)
        self.kind=kind;self.limit=initial_limit(kind);self.force=force;self.notice=notice
        self.keyword='';self.page=0
        self.selected=[r['name'] for r in held(gid,cid) if r['source']=='initial'][:self.limit]
        self.refresh()
    def pool(self):return tags(self.gid,query=self.keyword)
    def caption(self):
        text=f'🏷️ 角色 #{self.cid} 初始標籤（最多 {self.limit} 個）\n已選：'+('、'.join(self.selected) or '無')
        if self.notice:text+='\n⚠️ '+self.notice
        return text+f'\n第 {self.page+1} 頁 / {max(1,(len(self.pool())+24)//25)} 頁'
    def refresh(self):
        for item in list(self.children):
            if isinstance(item,discord.ui.Select):self.remove_item(item)
        self.add_item(TagPick(self))
    async def interaction_check(self,i):
        if i.guild_id!=self.gid or i.user.id!=self.actor_id:
            await i.response.send_message('這不是你的標籤選擇面板。',ephemeral=True);return False
        return True
    @discord.ui.button(label='上一頁',style=discord.ButtonStyle.secondary,row=1)
    async def previous(self,i,b):
        self.page=max(0,self.page-1);self.refresh();await i.response.edit_message(content=self.caption(),view=self)
    @discord.ui.button(label='下一頁',style=discord.ButtonStyle.secondary,row=1)
    async def next(self,i,b):
        self.page=min(max(0,(len(self.pool())-1)//25),self.page+1);self.refresh();await i.response.edit_message(content=self.caption(),view=self)
    @discord.ui.button(label='搜尋',style=discord.ButtonStyle.secondary,row=1)
    async def search(self,i,b):await i.response.send_modal(TagSearch(self))
    @discord.ui.button(label='清除選擇',style=discord.ButtonStyle.secondary,row=2)
    async def clear(self,i,b):self.selected=[];self.refresh();await i.response.edit_message(content=self.caption(),view=self)
    @discord.ui.button(label='保存初始標籤',style=discord.ButtonStyle.success,row=2)
    async def save(self,i,b):
        try: replace_initial(self.gid,self.cid,self.owner_id,i.user.id,self.selected,force=self.force)
        except (ValueError,PermissionError) as exc:return await i.response.send_message(f'⚠️ {exc}',ephemeral=True)
        await i.response.edit_message(content=f'✅ 角色 #{self.cid} 初始標籤：'+('、'.join(self.selected) or '無'),view=None)

async def show_tag_picker(i,cid,kind,owner_id,*,force=False,notice=''):
    try:
        view = TagSelectionView(i.guild_id, cid, owner_id, i.user.id, kind, force=force, notice=notice)
        caption = view.caption()
        if i.response.is_done():
            await i.followup.send(caption, view=view, ephemeral=True)
        else:
            await i.response.send_message(caption, view=view, ephemeral=True)
    except Exception as exc:
        LOG.exception('角色標籤選單開啟失敗 guild=%s character=%s',i.guild_id,cid)
        msg = f'⚠️ 標籤選單開啟失敗（{type(exc).__name__}），請查看 Railway Logs。'
        if i.response.is_done():
            await i.followup.send(msg, ephemeral=True)
        else:
            await i.response.send_message(msg, ephemeral=True)

async def setup(bot:commands.Bot):pass
