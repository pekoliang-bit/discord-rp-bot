"""Company bank panel displayed from /銀行; does not register a new command."""
from __future__ import annotations
import asyncio
import logging
from uuid import uuid4
import discord
from modules.company_treasury_ui import manageable_finance_businesses
from modules.company_treasury import company_bank_summary, move_company_bank
from modules.company_bank_history import format_company_bank_history
from modules.company_bank_applications import format_company_application_status
LOG=logging.getLogger(__name__)

async def _reply(interaction, work):
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        value=await asyncio.to_thread(work)
    except (ValueError,PermissionError) as error:
        value=f'⚠️ {error}'
    except Exception:
        LOG.exception('company bank operation failed')
        value='⚠️ 操作失敗，請聯絡管理員查看錯誤紀錄。'
    await interaction.followup.send(str(value)[:1900],ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

class AmountModal(discord.ui.Modal):
    def __init__(self,guild_id,user_id,business_id,direction):
        super().__init__(title='公司銀行存款' if direction=='deposit' else '公司銀行提款')
        self.gid,self.uid,self.bid,self.direction=str(guild_id),str(user_id),str(business_id),direction
        self.amount=discord.ui.TextInput(label='金額（金幣）',placeholder='例如：5000',max_length=15)
        self.add_item(self.amount)

    async def on_submit(self,interaction):
        if str(interaction.user.id)!=self.uid or str(interaction.guild_id)!=self.gid:
            return await interaction.response.send_message('此操作表單不屬於你。',ephemeral=True)
        try:
            amount=int(str(self.amount.value).replace(',','').strip())
            if not 1<=amount<=10**15: raise ValueError('金額必須大於零且不超過上限。')
        except ValueError:
            return await interaction.response.send_message('請輸入有效的正整數金額。',ephemeral=True)
        key=f'company-bank-ui:{interaction.id}:{uuid4().hex}'
        await _reply(interaction,lambda:move_company_bank(self.gid,self.bid,self.uid,self.direction,amount,key))

class AccountView(discord.ui.View):
    def __init__(self,guild_id,user_id,business_id):
        super().__init__(timeout=600)
        self.gid,self.uid,self.bid=str(guild_id),str(user_id),str(business_id)

    async def interaction_check(self,interaction):
        if str(interaction.guild_id)!=self.gid or str(interaction.user.id)!=self.uid:
            await interaction.response.send_message('這不是你的公司銀行面板。',ephemeral=True)
            return False
        return True

    @discord.ui.button(label='💰 公司存款',style=discord.ButtonStyle.success)
    async def deposit(self,interaction,button):
        await interaction.response.send_modal(AmountModal(self.gid,self.uid,self.bid,'deposit'))

    @discord.ui.button(label='💵 公司提款',style=discord.ButtonStyle.primary)
    async def withdraw(self,interaction,button):
        await interaction.response.send_modal(AmountModal(self.gid,self.uid,self.bid,'withdraw'))

    @discord.ui.button(label='📜 交易紀錄',style=discord.ButtonStyle.secondary)
    async def history(self,interaction,button):
        await _reply(interaction,lambda:format_company_bank_history(self.gid,self.bid,self.uid))

    @discord.ui.button(label='🔄 查看餘額',style=discord.ButtonStyle.secondary)
    async def balance(self,interaction,button):
        await _reply(interaction,lambda:company_bank_summary(self.gid,self.bid,self.uid))

class CompanySelect(discord.ui.Select):
    def __init__(self, businesses):
        options=[discord.SelectOption(label=str(x['name'])[:100],value=str(x['business_id']),description=f"公司編號：{str(x['business_id'])}"[:100]) for x in businesses]
        super().__init__(placeholder='選擇公司銀行帳戶',options=options,min_values=1,max_values=1)

    async def callback(self,interaction):
        view=self.view
        bid=self.values[0]
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            text=await asyncio.to_thread(company_bank_summary,view.gid,bid,view.uid)
            await interaction.followup.send(text,view=AccountView(view.gid,view.uid,bid),ephemeral=True)
        except (PermissionError,ValueError) as error:
            await interaction.followup.send(f'⚠️ {error}',ephemeral=True)
        except Exception:
            LOG.exception('company account lookup failed')
            await interaction.followup.send('⚠️ 公司帳戶讀取失敗。',ephemeral=True)

class CompanyPicker(discord.ui.View):
    def __init__(self,gid,uid,businesses,page=0):
        super().__init__(timeout=600)
        self.gid,self.uid=str(gid),str(uid)
        self.businesses,self.page=businesses,page
        start=page*25
        self.add_item(CompanySelect(businesses[start:start+25]))
        if page>0:
            self.add_item(NavButton('◀️ 上一頁',page-1))
        if start+25<len(businesses):
            self.add_item(NavButton('▶️ 下一頁',page+1))

    async def interaction_check(self,interaction):
        if str(interaction.user.id)!=self.uid or str(interaction.guild_id)!=self.gid:
            await interaction.response.send_message('這不是你的公司銀行選單。',ephemeral=True)
            return False
        return True

class NavButton(discord.ui.Button):
    def __init__(self,label,page):
        super().__init__(label=label,style=discord.ButtonStyle.secondary,row=1)
        self.page=page
    async def callback(self,interaction):
        v=self.view
        await interaction.response.edit_message(view=CompanyPicker(v.gid,v.uid,v.businesses,self.page))

async def open_company_banking(interaction):
    gid,uid=interaction.guild_id,interaction.user.id
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        businesses=[x for x in await asyncio.to_thread(manageable_finance_businesses,gid,uid) if x['kind']=='company']
        if not businesses:
            return await interaction.followup.send('你沒有可管理的公司。公司銀行帳戶需先透過 `/申請` 核准開通。',ephemeral=True)
        await interaction.followup.send('🏢 **公司銀行帳戶**\n請選擇公司，已開戶的公司可管理帳務；尚未開戶請至 `/申請`。',view=CompanyPicker(gid,uid,businesses),ephemeral=True)
    except Exception:
        LOG.exception('company bank entry failed')
        await interaction.followup.send('⚠️ 公司銀行入口暫時無法讀取。',ephemeral=True)

async def open_company_applications(interaction):
    await _reply(interaction,lambda:format_company_application_status(interaction.guild_id,interaction.user.id))
