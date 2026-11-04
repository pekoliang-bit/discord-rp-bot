
"""Company finance ledger classification panel. No extra slash command."""
from __future__ import annotations
import asyncio
import logging
import discord
from core.company_accounting import classify, format_report
LOG=logging.getLogger(__name__)

class ClassificationModal(discord.ui.Modal, title='公司帳本｜交易科目核定'):
    transaction_id=discord.ui.TextInput(label='帳本交易編號',max_length=20)
    category=discord.ui.TextInput(label='科目代碼（詳見報表）',max_length=32)
    reason=discord.ui.TextInput(label='核定依據（至少8字）',style=discord.TextStyle.paragraph,max_length=500)
    def __init__(self,gid,bid,uid):
        super().__init__();self.gid,self.bid,self.uid=str(gid),str(bid),str(uid)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人操作',ephemeral=True)
        await i.response.defer(ephemeral=True)
        try:
            msg=await asyncio.to_thread(classify,self.gid,self.bid,self.uid,self.transaction_id.value,self.category.value,self.reason.value)
        except (ValueError,PermissionError,LookupError) as e:msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('公司會計分類失敗');msg='⚠️ 分類失敗，請查詢日誌'
        await i.followup.send(msg[:1900],ephemeral=True)

class AccountingView(discord.ui.View):
    def __init__(self,gid,bid,uid):
        super().__init__(timeout=300);self.gid,self.bid,self.uid=map(str,(gid,bid,uid))
    async def interaction_check(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            await i.response.send_message('非本人面板',ephemeral=True);return False
        return True
    @discord.ui.button(label='📊 分類試算',style=discord.ButtonStyle.primary)
    async def report(self,i,b):
        await i.response.defer(ephemeral=True)
        try:msg=await asyncio.to_thread(format_report,self.gid,self.bid,self.uid)
        except (ValueError,PermissionError) as e:msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('會計報表失敗');msg='⚠️ 無法生成報表'
        await i.followup.send(msg[:1900],ephemeral=True)
    @discord.ui.button(label='✏️ 核定交易科目',style=discord.ButtonStyle.secondary)
    async def classify(self,i,b):
        await i.response.send_modal(ClassificationModal(self.gid,self.bid,self.uid))

class AccountingEntry(discord.ui.Button):
    def __init__(self):super().__init__(label='📚 會計科目',style=discord.ButtonStyle.secondary,row=2)
    async def callback(self,i):
        v=self.view
        await i.response.send_message('科目：operating_income、other_income、refund、cost_of_sales、payroll、operating_expense、tax、loan、equity、loan_repayment、owner_withdrawal、other_nonoperating。只有公司持有人可核定。',view=AccountingView(v.guild_id,v.business_id,v.user_id),ephemeral=True)
