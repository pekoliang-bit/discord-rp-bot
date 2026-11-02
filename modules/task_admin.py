"""任務專用官方管理面板；由 content_builder 路由，不註冊新斜線指令。"""
from __future__ import annotations
import discord
from modules.task_repository import initialize,publish_task,KINDS
from core.database import get_connection,transaction

KINDS_LABEL={'newbie':'新手','daily':'日常','screen':'影視','variety':'綜藝','endorsement':'代言','limited':'限時','special':'特殊'}

def authorized(i):
    # 群主可無限制操作；其他管理員的任務授權尚未完成映射，預設不放行。
    return bool(i.guild and i.user.id==i.guild.owner_id)

class TaskModal(discord.ui.Modal,title='建立任務草稿'):
    kind=discord.ui.TextInput(label='分類代碼',placeholder='daily / screen / variety / endorsement / limited / special',max_length=20)
    code=discord.ui.TextInput(label='唯一識別碼（英數）',max_length=60)
    name=discord.ui.TextInput(label='任務名稱',max_length=100)
    desc=discord.ui.TextInput(label='任務說明',style=discord.TextStyle.paragraph,max_length=800)
    subtype=discord.ui.TextInput(label='作品類型（如：電影、短劇）',required=False,max_length=60)
    async def on_submit(self,i):
        if not authorized(i):return await i.response.send_message('權限不足',ephemeral=True)
        kind=str(self.kind).strip();code=str(self.code).strip()
        if kind not in KINDS or kind=='newbie':return await i.response.send_message('新手任務請透過預設清單管理；分類代碼不正確。',ephemeral=True)
        if not code.replace('_','').replace('-','').isalnum():return await i.response.send_message('識別碼格式錯誤',ephemeral=True)
        try:
            publish_task(i.guild_id,code,kind,str(self.name),description=str(self.desc),subtype=str(self.subtype),status='draft')
        except Exception as e:return await i.response.send_message('建立失敗：'+str(e)[:170],ephemeral=True)
        await i.response.send_message('已建立草稿。發布前需設定完成條件、獎勵、正式等級模板；草稿不會對玩家顯示。',ephemeral=True)

class TaskAdminView(discord.ui.View):
    def __init__(self):super().__init__(timeout=300)
    async def interaction_check(self,i):
        if not authorized(i):await i.response.send_message('需要群主或授權任務管理人員的權限（目前僅已接線群主）。',ephemeral=True);return False
        return True
    @discord.ui.button(label='建立任務草稿',emoji='📝')
    async def create(self,i,b):await i.response.send_modal(TaskModal())
    @discord.ui.button(label='分類與等級設定說明',emoji='🎚️')
    async def levels(self,i,b):
        await i.response.send_message('通告分類：影視、綜藝、代言、限時、特殊。\n每種作品類型獨立設定第 1～5 級的咖位與屬性門檻；目前尚未設定正式模板的作品不能接取。\n玩家端只呈現最低接取條件。',ephemeral=True)
    @discord.ui.button(label='草稿及已發布清單',emoji='📋')
    async def listings(self,i,b):
        initialize()
        with get_connection() as db:
            rows=db.execute('SELECT id,code,title,kind,status FROM rp_task_definitions WHERE guild_id=? ORDER BY id DESC LIMIT 20',(str(i.guild_id),)).fetchall()
        text='\n'.join(f"#{r['id']} [{r['kind']}/{r['status']}] {r['title']} ({r['code']})" for r in rows) or '尚無任務'
        await i.response.send_message(text[:1900],ephemeral=True)

async def open_task_admin(i):
    if not authorized(i):await i.response.send_message('尚無此功能權限',ephemeral=True);return
    initialize()
    await i.response.edit_message(content='📋 **官方任務管理**\n草稿可建立與檢視；發布前需完成資格模板與獎勵審核，避免建立不可執行任務。',view=TaskAdminView())
