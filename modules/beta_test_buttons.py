"""創角和官方管理共用的內測角色按鈕（不額外註冊 Slash）。"""
from __future__ import annotations
import asyncio
import logging
import discord
from core import beta_test_core as beta

LOG=logging.getLogger(__name__)
ROLE_NAME='內測玩家'

def eligible(interaction):
    if interaction.guild is None:return False
    if interaction.user.id==interaction.guild.owner_id:return True
    perms=interaction.user.guild_permissions
    if perms.administrator:return True
    return any(role.name==ROLE_NAME for role in getattr(interaction.user,'roles',()))

class ClaimBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧪 領取測試角',style=discord.ButtonStyle.primary,row=1)
    async def callback(self,i):
        if not eligible(i):return await i.response.send_message('需要管理員權限或「內測玩家」身分組。',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(beta.setup)
            cid,created=await asyncio.to_thread(beta.claim,i.guild_id,i.user.id)
            await i.followup.send(('✅ 已隨機生成' if created else '你已領過')+f'測試角色 #{cid}。\n'
                '⚠️ 此版是安全的測試身分與轉正流程；各遊戲模組的金幣、行動值無限豁免仍待逐一接入。',ephemeral=True)
        except Exception as exc:
            LOG.exception('領取測試角色失敗')
            await i.followup.send(f'⚠️ {exc}',ephemeral=True)

class EditBetaModal(discord.ui.Modal,title='編輯我的內測角色'):
    name=discord.ui.TextInput(label='角色姓名',max_length=80)
    age=discord.ui.TextInput(label='年齡（0～200）',default='25',max_length=3)
    occupation=discord.ui.TextInput(label='職業',default='內測角色',max_length=80)
    biography=discord.ui.TextInput(label='人物設定',style=discord.TextStyle.paragraph,
        required=False,max_length=1800)
    async def on_submit(self,i):
        await i.response.defer(ephemeral=True)
        try:
            cid=await asyncio.to_thread(beta.edit,i.guild_id,i.user.id,
                self.name.value,self.age.value,self.occupation.value,self.biography.value)
            await i.followup.send(f'✅ 測試角色 #{cid} 的資料已儲存。',ephemeral=True)
        except Exception as exc:
            await i.followup.send(f'⚠️ {exc}',ephemeral=True)

class EditBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='📝 編輯測試角',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        if not eligible(i):
            return await i.response.send_message('需要管理員權限或內測玩家身分組。',ephemeral=True)
        await i.response.send_modal(EditBetaModal())

class ConvertBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧪 測試角色轉正草稿',style=discord.ButtonStyle.secondary,row=1)
    async def callback(self,i):
        await i.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(beta.setup)
            cid,created=await asyncio.to_thread(beta.convert_to_draft,i.guild_id,i.user.id)
            await i.followup.send(f'✅ 原創角色草稿 #{cid} '+('已建立' if created else '已存在')+
                '。請從「繼續角色草稿」依正式配點和兩階段審核送審。\n'
                '測試金幣、用品、屬性、行動值、公司及其他資產不繼承。',ephemeral=True)
        except Exception as exc:
            await i.followup.send(f'⚠️ {exc}',ephemeral=True)

class FinishBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧪 內測結束',style=discord.ButtonStyle.danger,row=1)
    async def callback(self,i):
        if i.guild is None or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('⛔ 僅群主可正式結束內測。',ephemeral=True)
        await i.response.send_message('按確認後全服內測角色將停用，並開放在創角草稿轉正。此動作不能從面板復原。',
            view=ConfirmEndBeta(i.user.id),ephemeral=True)

class ConfirmEndBeta(discord.ui.View):
    def __init__(self,uid):super().__init__(timeout=180);self.uid=uid
    @discord.ui.button(label='確認結束內測',style=discord.ButtonStyle.danger)
    async def confirm(self,i,button):
        if i.guild is None or i.user.id!=self.uid or i.user.id!=i.guild.owner_id:
            return await i.response.send_message('沒有操作權限。',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(beta.setup)
            count=await asyncio.to_thread(beta.finish,i.guild_id,i.user.id)
            await i.followup.send(f'✅ 內測已結束；停用 {count} 隻內測角色。玩家現在可從創角草稿申請轉正。',ephemeral=True)
        except Exception:
            LOG.exception('結束內測失敗')
            await i.followup.send('⚠️ 結束內測失敗，請查看 Railway Logs。',ephemeral=True)
