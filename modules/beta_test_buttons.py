"""創角和官方管理共用的內測角色按鈕（不額外註冊 Slash）。"""
from __future__ import annotations
import asyncio
import logging
import discord
from core import beta_test_core as beta

LOG=logging.getLogger(__name__)
class ClaimBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧪 領取測試角',style=discord.ButtonStyle.primary,row=1)
    async def callback(self,i):
        if i.guild is None:return await i.response.send_message('請在伺服器使用。',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(beta.setup)
            cid,created=await asyncio.to_thread(beta.claim,i.guild_id,i.user.id)
            from modules.character_forums import ensure_activated_forum
            forum_result=await ensure_activated_forum(i.client,i.guild_id,cid)
            forum_text=('\n✅ 私人角色論壇已建立或確認：<#'+forum_result['forum_id']+'>'
                if forum_result.get('status')=='ready' else
                '\n⏳ 私人角色論壇尚未建好，背景掃描將繼續補建（請確認男女分類及機器人管理頻道權限）。')
            await i.followup.send(('✅ 已隨機生成' if created else '你已領過')+f'測試角色 #{cid}。'+forum_text+'\n'
                '⚠️ 測試角色資源與正式經濟隔離。不是所有遊玩介面都已完成沙盒接入，請勿對正式玩家交易。',ephemeral=True)
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
        if not await asyncio.to_thread(beta.has_beta,i.guild_id,i.user.id):
            return await i.response.send_message('尚未領取測試角色。',ephemeral=True)
        await i.response.send_modal(EditBetaModal())

class ConvertBetaButton(discord.ui.Button):
    def __init__(self):super().__init__(label='🧪 測試角色轉正草稿',style=discord.ButtonStyle.secondary,row=1)
    async def callback(self,i):
        if not await asyncio.to_thread(beta.has_beta,i.guild_id,i.user.id):
            return await i.response.send_message('尚未領取測試角色。',ephemeral=True)
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
        await i.response.send_message('按確認後將結束公開內測，測試角色仍保留為永久測試專用；可建立正式原創草稿。此動作不可由面板復原。',
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
            await i.followup.send(f'✅ 內測已結束；{count} 隻測試角保留為測試專用。玩家可從創角草稿申請轉正。',ephemeral=True)
        except Exception:
            LOG.exception('結束內測失敗')
            await i.followup.send('⚠️ 結束內測失敗，請查看 Railway Logs。',ephemeral=True)
