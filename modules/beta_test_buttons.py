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
            forum_text=('\n✅ 私人角色論壇已建立或確認：<#'+forum_result['forum_id']+'>' +
                '\n🔗 私人論壇連結：https://discord.com/channels/'+str(i.guild_id)+'/'+forum_result['forum_id']
                if forum_result.get('status')=='ready' else
                '\n⏳ 私人角色論壇建立尚未完成：'+str(forum_result.get('reason','未知原因'))+
                '\n已使用指定測試論壇入口；請管理員確認它所屬的 Discord 分類與機器人建立頻道權限。')
            seq=await asyncio.to_thread(beta.draw_number,i.guild_id,cid)
            def load_beta_details():
                from core.database import get_connection
                import json
                with get_connection() as db:
                    row=db.execute("SELECT name,age,settings_json FROM characters WHERE guild_id=? AND id=?",
                                   (str(i.guild_id),int(cid))).fetchone()
                if row is None:
                    return ('（資料讀取失敗）','未知','未知')
                try:
                    data=json.loads(row['settings_json'] or '{}')
                except (ValueError,TypeError):
                    data={}
                return (str(row['name']),str(data.get('gender','未知')),str(row['age'] or '未填'))
            name,gender,age=await asyncio.to_thread(load_beta_details)
            if created and forum_result.get('status') == 'ready':
                private_link=(f'https://discord.com/channels/{i.guild_id}/'
                              f'{forum_result["forum_id"]}')
                try:
                    await i.user.send(
                        f'🎲 你抽中了測試角色 **{discord.utils.escape_markdown(name)}**！\n'
                        f'本輪序號：#{await asyncio.to_thread(beta.draw_number,i.guild_id,cid)}\n'
                        f'🔒 你的私人角色論壇：{private_link}\n'
                        '論壇已建立「通知、詢問、劇情討論、機器人使用」四個固定帖子。',
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    LOG.info('測試角色 #%s DM 未送達，領取者仍可從私人互動回覆開啟論壇',cid)
            intro=('✅ 已成功領取測試角色！' if created else '你已領取過測試角色。')
            await i.followup.send(
                f'{intro}\n🎲 本輪抽角序號：#{seq}\n'
                f'👤 角色姓名：**{discord.utils.escape_markdown(name)}**\n'
                f'⚧ 性別：{gender}｜年齡：{age}\n'
                f'🪪 角色資料庫識別 ID：{cid}（非抽角序號）'
                +forum_text+'\n'
                '📝 修改人設：/創角 → 編輯測試角\n'
                '⚠️ 測試資源與正式經濟隔離；部分介面尚未完成沙盒接入，請勿與正式角色交易。',
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none())
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
