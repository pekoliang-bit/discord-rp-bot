"""代言合約期限、續約與終止 UI；不新增斜線指令。"""
import asyncio
import logging
import discord
from core import company_endorsement_lifecycle as lifecycle
from core import company_endorsement_breach as breach
LOG=logging.getLogger(__name__)

class TermsModal(discord.ui.Modal):
    def __init__(self,gid,uid,action):
        self.gid,self.uid,self.action=str(gid),str(uid),action
        super().__init__(title={'propose':'提出／續約代言條款','approve':'確認代言條款','details':'查詢合約條款','terminate':'終止代言','expire':'更新合約到期狀態','history':'查詢條款歷史'}[action])
        labels={'propose':['代言編號','合約到期日 YYYY-MM-DD','雙方約定違約金（0為未約定）'], 'approve':['代言編號'], 'details':['代言編號'],'terminate':['代言編號','終止理由'],'expire':['代言編號'],'history':['代言編號']}
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
            elif self.action=='expire':
                count=await asyncio.to_thread(lifecycle.reconcile_expired,self.gid,int(v[0]));msg=f'✅ 已整理 {count} 筆到期代言；不會自動扣款'
            elif self.action=='history':
                rows=await asyncio.to_thread(lifecycle.history,self.gid,int(v[0]),self.uid)
                msg='📜 **合約異動紀錄**\n'+('\n'.join(f"{r['created_at']}｜{r['event']}｜{r['details']}" for r in rows) if rows else '尚無異動紀錄')
            elif self.action=='terminate':
                res=await asyncio.to_thread(lifecycle.terminate,self.gid,int(v[0]),self.uid,v[1]);msg=f"✅ 已終止；約定違約金 {res['agreed_breach_amount']:,}。{res['notice']}"
            else:
                res=await asyncio.to_thread(lifecycle.details,self.gid,int(v[0]),self.uid)
                msg=('尚未設定合約期限' if not res else f"📜 合約 v{res['version']}｜到期 {res['end_date']}｜違約金 {res['breach_amount']:,}｜狀態 {res['status']}")
                if res:
                    renewal=await asyncio.to_thread(lifecycle.renewal_status,self.gid,int(v[0]),self.uid)
                    msg+=f"｜剩餘 {renewal['days_left']} 天"
                    if renewal['needs_renewal']: msg+='｜⚠️ 建議提出續約'
        except (ValueError,PermissionError,LookupError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('代言合約操作失敗');msg='⚠️ 操作失敗，請查看日誌'
        await i.followup.send(msg[:1900],ephemeral=True)

class BreachModal(discord.ui.Modal):
    def __init__(self,gid,uid,mode):
        super().__init__(title='違約和解協議'+('提出' if mode=='propose' else '確認'))
        self.gid,self.uid,self.mode=str(gid),str(uid),mode
        names=['代言編號','付款方 Discord ID','協議金額','協議理由'] if mode=='propose' else ['代言編號']
        self.inputs=[]
        for name in names:
            item=discord.ui.TextInput(label=name,max_length=200);self.add_item(item);self.inputs.append(item)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        v=[x.value.strip() for x in self.inputs]
        try:
            if self.mode=='propose': msg=await asyncio.to_thread(breach.propose,self.gid,int(v[0]),self.uid,v[1],int(v[2]),v[3])
            else: msg=await asyncio.to_thread(breach.accept,self.gid,int(v[0]),self.uid)
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('違約協議操作失敗');msg='⚠️ 操作失敗，請查日誌'
        await i.followup.send(msg[:1900],ephemeral=True)

class BreachAction(discord.ui.Button):
    def __init__(self,mode): super().__init__(label='⚖️ '+('提出和解' if mode=='propose' else '同意和解'),style=discord.ButtonStyle.secondary,row=1);self.mode=mode
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(BreachModal(v.gid,v.uid,self.mode))

class TermsAction(discord.ui.Button):
    def __init__(self,action,label): super().__init__(label=label,style=discord.ButtonStyle.secondary,row=0 if action in ('propose','approve','details','terminate','expire') else 1);self.action=action
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(TermsModal(v.gid,v.uid,self.action))


class SettlementPayModal(discord.ui.Modal):
    def __init__(self,gid,uid):
        super().__init__(title='支付已同意的代言和解')
        self.gid,self.uid=str(gid),str(uid)
        self.eid=discord.ui.TextInput(label='代言編號',max_length=20)
        self.source=discord.ui.TextInput(label='付款人的角色編號',max_length=20)
        self.dest=discord.ui.TextInput(label='收款人的角色編號',max_length=20)
        for f in (self.eid,self.source,self.dest): self.add_item(f)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            msg=await asyncio.to_thread(breach.pay_settlement,self.gid,int(self.eid.value),self.uid,int(self.source.value),int(self.dest.value))
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('和解支付失敗');msg='⚠️ 付款未完成，請查看日誌'
        await i.followup.send(msg,ephemeral=True)

class SettlementPayAction(discord.ui.Button):
    def __init__(self): super().__init__(label='💸 支付和解',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        if str(i.guild_id)!=self.view.gid or str(i.user.id)!=self.view.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(SettlementPayModal(self.view.gid,self.view.uid))

class SettlementDetailsModal(discord.ui.Modal):
    def __init__(self, gid, uid):
        super().__init__(title='查看違約和解狀態')
        self.gid, self.uid = str(gid), str(uid)
        self.endorsement = discord.ui.TextInput(label='代言編號', max_length=20)
        self.add_item(self.endorsement)

    async def on_submit(self, interaction):
        if str(interaction.guild_id) != self.gid or str(interaction.user.id) != self.uid:
            return await interaction.response.send_message('非本人表單', ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            record = await asyncio.to_thread(breach.settlement_details, self.gid, int(self.endorsement.value), self.uid)
            if record is None:
                message = '尚未建立違約和解協議'
            else:
                status = '已付款' if record.get('paid_at') else record['status']
                message = f"⚖️ 代言 #{record['endorsement_id']}｜{status}｜金額 {record['amount']:,} 金幣｜{record['reason']}"
        except (ValueError, PermissionError) as exc:
            message = f'⚠️ {exc}'
        except Exception:
            LOG.exception('查詢違約和解失敗')
            message = '⚠️ 查詢失敗，請查看日誌'
        await interaction.followup.send(message[:1900], ephemeral=True)


class SettlementDetailsAction(discord.ui.Button):
    def __init__(self):
        super().__init__(label='📄 和解狀態', style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction):
        view = self.view
        if str(interaction.guild_id) != view.gid or str(interaction.user.id) != view.uid:
            return await interaction.response.send_message('非本人面板', ephemeral=True)
        await interaction.response.send_modal(SettlementDetailsModal(view.gid, view.uid))


class TermsView(discord.ui.View):
    def __init__(self,gid,uid):
        super().__init__(timeout=300);self.gid,self.uid=str(gid),str(uid)
        for a,l in [('propose','📅 提出／續約'),('approve','✍️ 藝人同意'),('details','📜 查詢條款'),('terminate','🛑 終止代言'),('expire','⌛ 到期整理'),('history','📚 條款歷史')]: self.add_item(TermsAction(a,l))
        self.add_item(BreachAction('propose'))
        self.add_item(BreachAction('accept'))
        self.add_item(SettlementPayAction())
        self.add_item(SettlementDetailsAction())

class TermsEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='📅 代言合約',style=discord.ButtonStyle.secondary,row=3)
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_message('📅 **代言期限與條款**：品牌提出 → 藝人同意。終止不自動扣除違約金。',view=TermsView(v.gid,v.uid),ephemeral=True)
