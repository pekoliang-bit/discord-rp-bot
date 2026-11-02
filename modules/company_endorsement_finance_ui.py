"""代言期款管理入口，掛在 /公司 → 商務管理。"""
from __future__ import annotations
import asyncio, logging
import discord
from core import company_endorsement_finance as finance
LOG=logging.getLogger(__name__)

class EndorsementPaymentModal(discord.ui.Modal):
    def __init__(self,gid,uid,action):
        super().__init__(title={'offer':'建立代言期款','accept':'藝人確認期款','pay':'支付代言期款','history':'查詢代言期款','cancel':'取消未付期款','due':'待付期款清單'}[action],timeout=300)
        self.gid,self.uid,self.action=str(gid),str(uid),action
        specs={'offer':['代言紀錄編號','付款公司或品牌編號','金額（金幣）','期款名稱／期數','到期日 YYYY-MM-DD（可留空）'], 'accept':['期款編號'],'pay':['期款編號'],'cancel':['期款編號'],'history':['代言紀錄編號'],'due':['代言紀錄編號']}
        self.fields=[]
        for label in specs[action]:
            f=discord.ui.TextInput(label=label,max_length=100,required=not label.startswith('到期日'));self.add_item(f);self.fields.append(f)
    async def on_submit(self,interaction):
        if str(interaction.guild_id)!=self.gid or str(interaction.user.id)!=self.uid:
            return await interaction.response.send_message('這不是你的表單',ephemeral=True)
        await interaction.response.defer(ephemeral=True,thinking=True)
        v=[f.value.strip() for f in self.fields]
        try:
            if self.action=='offer': msg=f'✅ 代言期款 #{await asyncio.to_thread(finance.offer,self.gid,int(v[0]),v[1],int(v[2]),v[3],self.uid,v[4] or None)} 已建立；請藝人確認'
            elif self.action=='accept': msg=await asyncio.to_thread(finance.accept,self.gid,int(v[0]),self.uid)
            elif self.action=='pay': msg=await asyncio.to_thread(finance.pay,self.gid,int(v[0]),self.uid)
            elif self.action=='cancel': msg=await asyncio.to_thread(finance.cancel,self.gid,int(v[0]),self.uid)
            elif self.action=='due':
                rows=await asyncio.to_thread(finance.due_installments,self.gid,int(v[0]),self.uid)
                msg='📅 **待付期款**\n'+('\n'.join(f"#{r['id']}｜{r['installment_label']}｜{r['amount']:,}｜{r['due_date'] or '未設定到期日'}｜{r['status']}" for r in rows) if rows else '暫無待付期款')
            else:
                rows=await asyncio.to_thread(finance.history,self.gid,int(v[0]),self.uid)
                msg='📜 **代言期款紀錄**\n'+('\n'.join(f"#{r['id']}｜{r['installment_label']}｜{r['amount']:,}｜{r['status']}" for r in rows) if rows else '暫無紀錄')
        except (ValueError,PermissionError,LookupError,RuntimeError) as exc: msg=f'⚠️ {exc}'
        except Exception:
            LOG.exception('代言期款操作失敗');msg='⚠️ 操作失敗，請查看機器人日誌'
        await interaction.followup.send(msg[:1900],ephemeral=True)

class PaymentAction(discord.ui.Button):
    def __init__(self,action,label): super().__init__(label=label,style=discord.ButtonStyle.secondary,row=2);self.action=action
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('這不是你的管理面板',ephemeral=True)
        await i.response.send_modal(EndorsementPaymentModal(v.gid,v.uid,self.action))

class EndorsementPaymentView(discord.ui.View):
    def __init__(self,gid,uid):
        super().__init__(timeout=300);self.gid,self.uid=str(gid),str(uid)
        for action,label in [('offer','💰 建立期款'),('accept','✍️ 確認期款'),('pay','💳 支付期款'),('history','📜 付款紀錄'),('cancel','✖️ 取消期款'),('due','📅 待付期款')]: self.add_item(PaymentAction(action,label))

class EndorsementFinanceEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='💰 代言期款',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('這不是你的管理面板',ephemeral=True)
        await i.response.send_message('💰 **代言期款管理**：品牌建立期款 → 藝人確認 → 品牌支付。已付款不可直接取消。',view=EndorsementPaymentView(v.gid,v.uid),ephemeral=True)
