"""董事長對集團唯讀職權的授權 UI。"""
import asyncio
import logging
import discord
from core import company_group_delegation as svc
from core.company_group_overview import group_overview, delegated_group_overview
LOG=logging.getLogger(__name__)

class DelegateModal(discord.ui.Modal):
    def __init__(self,gid,uid,mode):
        super().__init__(title={'grant':'授予集團查閱權','revoke':'取消集團查閱權'}[mode]);self.gid,self.uid,self.mode=str(gid),str(uid),mode
        self.delegate=discord.ui.TextInput(label='Discord 使用者 ID',max_length=25);self.add_item(self.delegate)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            obj=await asyncio.to_thread(group_overview,self.gid,self.uid)
            if not obj.get('group_id'): raise ValueError('你目前不是集團董事長')
            if self.mode=='grant': msg=await asyncio.to_thread(svc.grant,self.gid,obj['group_id'],self.uid,self.delegate.value.strip())
            else:
                ok=await asyncio.to_thread(svc.revoke,self.gid,obj['group_id'],self.uid,self.delegate.value.strip());msg='✅ 已取消授權' if ok else '沒有這筆授權'
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('集團授權失敗');msg='⚠️ 無法完成授權，請查看日誌'
        await i.followup.send(msg,ephemeral=True)

class DelegationAction(discord.ui.Button):
    def __init__(self,mode,label): super().__init__(label=label,style=discord.ButtonStyle.secondary);self.mode=mode
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        if self.mode in ('grant','revoke'):
            return await i.response.send_modal(DelegateModal(v.gid,v.uid,self.mode))
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            obj=await asyncio.to_thread(group_overview,v.gid,v.uid)
            if not obj.get('group_id'): raise ValueError('你目前不是集團董事長')
            rows=await asyncio.to_thread(svc.list_grants,v.gid,obj['group_id'],v.uid)
            msg='📋 集團授權名單\n'+('\n'.join(f"{x['delegate_id']}｜{x['capability']}" for x in rows[:20]) if rows else '暫無授權')
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('集團授權查詢失敗');msg='⚠️ 無法查詢授權'
        await i.followup.send(msg[:1900],ephemeral=True)

class DelegationView(discord.ui.View):
    def __init__(self,gid,uid):
        super().__init__(timeout=300);self.gid,self.uid=str(gid),str(uid)
        for a,l in [('grant','授予查閱'),('revoke','取消查閱'),('list','授權名單')]: self.add_item(DelegationAction(a,l))

class DelegationEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='🔐 集團授權',style=discord.ButtonStyle.secondary)
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_message('🔐 董事長可授予／撤銷資訊查閱權。此權限不涉及財務操作。',view=DelegationView(v.gid,v.uid),ephemeral=True)


class DelegatedOverviewModal(discord.ui.Modal):
    def __init__(self,gid,uid):
        super().__init__(title='集團授權資訊查閱')
        self.gid,self.uid=str(gid),str(uid)
        self.group=discord.ui.TextInput(label='集團編號',max_length=100)
        self.add_item(self.group)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            from core.company_group_overview import format_group_overview
            data=await asyncio.to_thread(delegated_group_overview,self.gid,self.uid,self.group.value.strip())
            msg=format_group_overview(data)
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('授權查閱失敗');msg='⚠️ 查閱失敗，請查看日誌'
        await i.followup.send(msg[:1900],ephemeral=True)

class DelegatedOverviewEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='📋 授權集團查閱',style=discord.ButtonStyle.secondary)
    async def callback(self,i):
        v=self.view
        if str(i.guild_id)!=v.gid or str(i.user.id)!=v.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(DelegatedOverviewModal(v.gid,v.uid))


class DelegatedCompaniesEntry(discord.ui.Button):
    def __init__(self): super().__init__(label='📋 授權公司清單',style=discord.ButtonStyle.secondary)
    async def callback(self,i):
        if str(i.guild_id)!=self.view.gid or str(i.user.id)!=self.view.uid:
            return await i.response.send_message('非本人面板',ephemeral=True)
        await i.response.send_modal(DelegatedCompaniesModal(self.view.gid,self.view.uid))

class DelegatedCompaniesModal(discord.ui.Modal):
    def __init__(self,gid,uid):
        super().__init__(title='查詢授權集團公司清單');self.gid,self.uid=gid,uid
        self.group=discord.ui.TextInput(label='集團編號',max_length=100);self.add_item(self.group)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message('非本人表單',ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            from core.company_group_overview import delegated_company_summary
            rows=await asyncio.to_thread(delegated_company_summary,self.gid,self.uid,self.group.value.strip())
            msg='🏢 **旗下公司清單**\n'+('\n'.join(f"{r['name']}（{r['business_id']}）" for r in rows) or '暫無公司')
        except (ValueError,PermissionError) as e: msg=f'⚠️ {e}'
        except Exception:
            LOG.exception('集團公司唯讀查詢失敗');msg='⚠️ 無法完成查詢'
        await i.followup.send(msg[:1900],ephemeral=True)
