
"""Optional entry for /公司 treasury: manually audited historical wages."""
import asyncio
import logging
import discord
from core import company_wage_debt as service
LOG=logging.getLogger(__name__)

class DebtModal(discord.ui.Modal):
    def __init__(self,gid,bid,uid,mode):
        super().__init__(title='登記歷史欠薪' if mode=='create' else '補發歷史欠薪')
        self.gid,self.bid,self.uid,self.mode=str(gid),str(bid),str(uid),mode
        if mode=='create':
            self.character=discord.ui.TextInput(label='角色編號',max_length=20)
            self.period=discord.ui.TextInput(label='薪資期識別（不可重複）',max_length=80)
            self.amount=discord.ui.TextInput(label='核定欠薪金額',max_length=20)
            self.reason=discord.ui.TextInput(label='核定依據／當時薪資說明',style=discord.TextStyle.paragraph,max_length=300)
            for field in (self.character,self.period,self.amount,self.reason):self.add_item(field)
        else:
            self.debt_id=discord.ui.TextInput(label='欠薪紀錄編號',max_length=20);self.add_item(self.debt_id)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('不是你的表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            if self.mode=='create':
                msg=await asyncio.to_thread(service.record_debt,self.gid,self.bid,self.uid,self.character.value,self.period.value,self.amount.value,self.reason.value)
            else:
                msg=await asyncio.to_thread(service.settle_debt,self.gid,self.bid,self.uid,self.debt_id.value)
        except (ValueError,PermissionError,RuntimeError) as e:msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('歷史薪資作業失敗');msg='⚠️ 作業失敗，請查閱日誌及正式帳本'
        await i.followup.send(msg[:1900],ephemeral=True)

class DebtView(discord.ui.View):
    def __init__(self,gid,bid,uid):
        super().__init__(timeout=300);self.gid,self.bid,self.uid=str(gid),str(bid),str(uid)
    async def interaction_check(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            await i.response.send_message('不是你的面板',ephemeral=True);return False
        return True
    @discord.ui.button(label='登記歷史欠薪',style=discord.ButtonStyle.secondary)
    async def create(self,i,b):await i.response.send_modal(DebtModal(self.gid,self.bid,self.uid,'create'))
    @discord.ui.button(label='補發指定欠薪',style=discord.ButtonStyle.secondary)
    async def pay(self,i,b):await i.response.send_modal(DebtModal(self.gid,self.bid,self.uid,'pay'))
    @discord.ui.button(label='欠薪清單',style=discord.ButtonStyle.primary)
    async def history(self,i,b):
        await i.response.defer(ephemeral=True)
        try:
            rows=await asyncio.to_thread(service.list_debts,self.gid,self.bid,self.uid)
            text='\n'.join(f'#{r["id"]} 角色{r["character_id"]} {r["period_label"]} {r["amount"]:,}｜{r["status"]}' for r in rows) or '尚無核定欠薪紀錄'
        except (ValueError,PermissionError) as e:text=f'⚠️ {e}'
        except Exception:
            LOG.exception('欠薪查詢失敗');text='⚠️ 欠薪查詢失敗'
        await i.followup.send(text[:1900],ephemeral=True)

class DebtEntry(discord.ui.Button):
    def __init__(self):super().__init__(label='📜 歷史欠薪',style=discord.ButtonStyle.secondary)
    async def callback(self,i):
        view=self.view
        if str(i.guild_id)!=view.gid or str(i.user.id)!=view.uid:
            return await i.response.send_message('不是你的面板',ephemeral=True)
        bid=getattr(view,'business_id',None) or getattr(view,'bid',None)
        if bid is None: return await i.response.send_message('請先選擇公司',ephemeral=True)
        await i.response.send_message('歷史欠薪核定與補發（僅公司持有人可操作）',view=DebtView(view.gid,bid,view.uid),ephemeral=True)
