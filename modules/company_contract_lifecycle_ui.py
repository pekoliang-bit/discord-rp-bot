"""代言合約期限、續約與終止 UI；不新增斜線指令。"""
import asyncio
import logging
import discord
from core import company_endorsement_lifecycle as lifecycle
LOG=logging.getLogger(__name__)

class TermsModal(discord.ui.Modal):
    def __init__(self,gid,uid,action):
        self.gid,self.uid,self.action=str(gid),str(uid),action
        super().__init__(title={'propose':'提出／續約代言條款','approve':'確認代言條款','details':'查詢合約條款','terminate':'終止代言'}[action])
        labels={'propose':['代言編號','合約到期日 YYYY-MM-DD','雙方約定違約金（0為未約定）'], 'approve':['代言編號'], 'details':['代言編號'],'terminate':['代言編號','終止理由']}
        self.fields=[]
        for label in labels[action]:
            field=discord.ui.TextInput(label=label,max_length=200);self.add_item(field);self.fields.append(field)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('這不是你的表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        v=[x.value.strip() for x in self.fields]
        try:
            if self.action=='propose':
                ver=await asyncio.to_thread(lifecycle.propose,self.gid,int(v[0]),self.uid,v[1],int(v[2]));msg=f'✅ 第 {ver} 版條款已提出，等待藝人確認'
            elif self.action=='approve': msg=await asyncio.to_thread(lifecycle.approve,self.gid,int(v[0]),self.uid)
            elif self.action=='terminate':
                res=await asyncio.to_thread(lifecycle.terminate,self.gid,int(v[0]),self.uid,v[1]);msg=f"✅ 已終止；約定違約金 {res['agreed_breach_amount']:,}。{res['notice']}"
            else:
                res=await asyncio.to_thread(lifecycle.details,self.gid,int(v[0]),self.uid)
                msg=('尚未設定合約期限' if not res else f"📜 合約 v{res['version']}｜到期 {res['end_date']}｜違約金 {res['breach_amount']:,}｜狀態 {res['status']}")
        except (ValueError,PermissionError,LookupError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('代言合約操作失敗');msg='⚠️ 操作失敗，請查看日誌'
        await i.followup.send(msg[:1900],ephemeral=True)

class TermsAction(discord.ui.Button):
    def __init__(self,action,label): super().__init__(label=label,style=discord.ButtonStyle.secondary);self.action=action
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(TermsModal(v.gid,v.uid,self.action))

class TermsView(discord.ui.View):
    def __init__(self,gid,uid):
        super().__init__(timeout=300);self.gid,self.uid=str(gid),str(uid)
        for a,l in [('propose','📅 提出／續約'),('approve','✍️ 藝人同意'),('details','📜 查詢條款'),('terminate','🛑 終止代言')]: self.add_item(TermsAction(a,l))

class TermsEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='📅 代言合約',style=discord.ButtonStyle.secondary,row=3)
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_message('📅 **代言期限與條款**：品牌提出 → 藝人同意。終止不自動扣除違約金。',view=TermsView(v.gid,v.uid),ephemeral=True)
