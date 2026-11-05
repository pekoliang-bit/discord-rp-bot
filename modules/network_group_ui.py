"""網路帳號 / 群組操作子面板；不另行註冊 slash 指令。"""
from __future__ import annotations
import asyncio
import logging
import re
import discord
from core import network_groups as ng

LOG=logging.getLogger(__name__)

class PrivateView(discord.ui.View):
    def __init__(self,owner_id,timeout=300):
        super().__init__(timeout=timeout); self.owner_id=int(owner_id)
    async def interaction_check(self,interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('⛔ 請使用自己的 /網路 面板。',ephemeral=True); return False
        return True

async def inform(interaction,text):
    if interaction.response.is_done(): await interaction.followup.send(text,ephemeral=True)
    else: await interaction.response.send_message(text,ephemeral=True)

class CreateAccountModal(discord.ui.Modal,title='建立角色網路帳號'):
    handle=discord.ui.TextInput(label='帳號名稱（不用輸入 @）',placeholder='支援中文、日文等其他語言',max_length=80)
    def __init__(self,character_id): super().__init__(); self.character_id=character_id
    async def on_submit(self,interaction):
        try:
            aid=await asyncio.to_thread(ng.create_account,interaction.guild.id,interaction.user.id,self.character_id,str(self.handle))
            await inform(interaction,f'✅ 已建立 @{self.handle}（帳號 #{aid}）。請重新使用 /網路 選擇帳號。')
        except (ValueError,PermissionError) as e: await inform(interaction,f'⚠️ {e}')
        except Exception: LOG.exception('帳號建立失敗'); await inform(interaction,'⚠️ 系統錯誤，請查看日誌')

class AccountPicker(PrivateView):
    def __init__(self,owner,character,accounts):
        super().__init__(owner); self.character=character
        if accounts:
            sel=discord.ui.Select(placeholder='選擇要使用的角色網路帳號',options=[discord.SelectOption(label=('@'+a['handle'])[:100],value=str(a['id'])) for a in accounts[:25]])
            async def chosen(interaction):
                account=int(sel.values[0])
                try:
                    a=await asyncio.to_thread(ng.account_info,interaction.guild.id,interaction.user.id,account)
                    await interaction.response.send_message(f"🌐 **網路首頁**｜{a['character_name']} · @{a['handle']}",view=NetworkHome(interaction.user.id,self.character,account),ephemeral=True)
                except (ValueError,PermissionError) as e: await inform(interaction,f'⚠️ {e}')
            sel.callback=chosen; self.add_item(sel)
    @discord.ui.button(label='建立新帳號',emoji='➕',style=discord.ButtonStyle.primary)
    async def create(self,interaction,button): await interaction.response.send_modal(CreateAccountModal(self.character))

class CharacterPicker(PrivateView):
    def __init__(self,owner,characters):
        super().__init__(owner)
        sel=discord.ui.Select(placeholder='第一步：選擇角色',options=[discord.SelectOption(label=f'{name[:70]} (#{cid})',value=str(cid)) for cid,name in characters[:25]])
        async def chosen(interaction):
            cid=int(sel.values[0])
            try:
                await asyncio.to_thread(ng.ensure_default_accounts,interaction.guild.id,cid)
                ac=await asyncio.to_thread(ng.accounts,interaction.guild.id,interaction.user.id,cid)
            except (ValueError,PermissionError) as e: await inform(interaction,f'⚠️ {e}'); return
            await interaction.response.send_message('第二步：選擇角色的網路帳號；沒有帳號可先建立。',view=AccountPicker(interaction.user.id,cid,ac),ephemeral=True)
        sel.callback=chosen; self.add_item(sel)

class NetworkHome(PrivateView):
    def __init__(self,owner,character,account):
        super().__init__(owner); self.character=character; self.account=account
    @discord.ui.button(label='直播中心',emoji='🔴',row=0)
    async def live(self,interaction,button):
        from modules.live_stream_ui import NetworkView
        # 使用既有直播功能，並將選中的角色鎖定，不再出現另一個角色選擇選單。
        a=await asyncio.to_thread(ng.account_info,interaction.guild.id,interaction.user.id,self.account)
        v=NetworkView(self.owner_id,[(self.character,a['character_name'])]); v.character_id=self.character
        await interaction.response.send_message('🔴 直播功能',view=v,ephemeral=True)
    @discord.ui.button(label='群組中心',emoji='👥',row=0)
    async def groups(self,interaction,button):
        await interaction.response.send_message('👥 **網路群組中心**',view=GroupsView(self.owner_id,self.character,self.account),ephemeral=True)
    @discord.ui.button(label='網友評論',emoji='💬',row=0)
    async def comments(self,interaction,button):
        await inform(interaction,'💬 評論模板與系統網友由事件觸發；玩家直接發布評論的操作尚未接入，不能冒用系統網友。')
    @discord.ui.button(label='網路輿論',emoji='📣',row=1)
    async def rumors(self,interaction,button):
        await inform(interaction,'📣 傳聞目前使用獨立的 /傳聞 系統；尚未完成網路首頁的直接連接。')
    @discord.ui.button(label='帳號資訊',emoji='@',row=1)
    async def info(self,interaction,button):
        a=await asyncio.to_thread(ng.account_info,interaction.guild.id,interaction.user.id,self.account)
        await inform(interaction,f"角色：{a['character_name']} #{a['character_id']}\n網路帳號：@{a['handle']}（#{a['id']}）")

class CreateGroupModal(discord.ui.Modal,title='建立網路群組'):
    group_name=discord.ui.TextInput(label='群組名稱',min_length=2,max_length=70)
    channel_name=discord.ui.TextInput(label='Discord 頻道名稱',min_length=2,max_length=80)
    def __init__(self,account): super().__init__(); self.account=account
    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True,thinking=True)
        guild=interaction.guild
        if not guild: await inform(interaction,'僅限伺服器'); return
        s=ng.settings(guild.id)
        category=guild.get_channel(s['category_id']) if s['category_id'] else None
        if not isinstance(category,discord.CategoryChannel):
            await inform(interaction,'⚠️ 群組分類未設定或無法存取；請先由管理員設定頻道分類。'); return
        me=guild.me
        if not me or not me.guild_permissions.manage_channels:
            await inform(interaction,'⚠️ 機器人需要「管理頻道」權限。'); return
        name=str(self.channel_name).strip()
        name=re.sub(r'\s+','-',name)
        if not name or len(name)>80 or '@everyone' in name:
            await inform(interaction,'⚠️ 不合法的頻道名稱'); return
        group=None; channel=None
        try:
            a=await asyncio.to_thread(ng.account_info,guild.id,interaction.user.id,self.account)
            group=await asyncio.to_thread(ng.begin_group,guild.id,interaction.user.id,self.account,str(self.group_name))
            overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                        guild.me:discord.PermissionOverwrite(view_channel=True,send_messages=True,manage_channels=True),
                        interaction.user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)}
            channel=await guild.create_text_channel(name,category=category,overwrites=overwrites,reason=f'網路群組 #{group}／{a["handle"]}')
            await asyncio.to_thread(ng.activate_group,guild.id,group,channel.id)
            await inform(interaction,f'✅ 已成立 **{self.group_name}**（#{group}）\n頻道：{channel.mention}\n請在群組中心邀請其他帳號。')
        except (ValueError,PermissionError,LookupError,discord.HTTPException) as e:
            if channel is not None:
                try: await channel.delete(reason='建立群組失敗，復原頻道')
                except discord.HTTPException: LOG.exception('群組建立失敗後，無法刪除孤立頻道')
            if group is not None: await asyncio.to_thread(ng.cancel_group,guild.id,group)
            await inform(interaction,f'⚠️ 建立失敗：{e}')
        except Exception:
            LOG.exception('建立群組失敗')
            if group is not None: await asyncio.to_thread(ng.cancel_group,guild.id,group)
            await inform(interaction,'⚠️ 群組建立失敗，請查看日誌')

class InviteGroupModal(discord.ui.Modal,title='邀請成員加入群組'):
    group_id=discord.ui.TextInput(label='群組編號',placeholder='例如 12')
    target=discord.ui.TextInput(label='角色名字 #編號，或 @帳號名稱',placeholder='小明 #1024 或 @ming')
    def __init__(self,account): super().__init__(); self.account=account
    async def on_submit(self,interaction):
        try:
            target=await asyncio.to_thread(ng.invite,interaction.guild.id,interaction.user.id,self.account,int(self.group_id.value),str(self.target.value))
            await inform(interaction,f"✅ 已邀請 {target['name']}（@{target['handle']}）。對方可由 /網路 → 該帳號 → 群組中心接受。")
        except (ValueError,LookupError,PermissionError) as e: await inform(interaction,f'⚠️ {e}')
        except Exception: LOG.exception('群組邀請失敗'); await inform(interaction,'⚠️ 系統錯誤')

class GroupActionModal(discord.ui.Modal,title='群組邀請／退出'):
    group_id=discord.ui.TextInput(label='群組編號',placeholder='輸入群組 #編號')
    def __init__(self,account,action): super().__init__(); self.account=account; self.action=action
    async def on_submit(self,interaction):
        guild=interaction.guild
        try:
            group=int(self.group_id.value)
            if self.action=='accept':
                result=await asyncio.to_thread(ng.accept,guild.id,interaction.user.id,self.account,group)
                channel=guild.get_channel(int(result['channel_id']))
                if not isinstance(channel,discord.TextChannel):
                    await asyncio.to_thread(ng.undo_accept,guild.id,interaction.user.id,self.account,group)
                    await inform(interaction,'⚠️ 頻道不存在；已保留邀請，請聯繫管理員。'); return
                # 同一 Discord 使用者可能有多個角色；權限以 Discord 使用者層級授予。
                try:
                    await channel.set_permissions(interaction.user,view_channel=True,send_messages=True,read_message_history=True,reason=f'加入網路群組 #{group}')
                except discord.HTTPException:
                    await asyncio.to_thread(ng.undo_accept,guild.id,interaction.user.id,self.account,group)
                    raise
                await inform(interaction,f'✅ 已加入 {result["name"]}：{channel.mention}')
            elif self.action=='decline':
                await asyncio.to_thread(ng.decline,guild.id,interaction.user.id,self.account,group)
                await inform(interaction,'✅ 已拒絕邀請')
            else:
                result=await asyncio.to_thread(ng.leave,guild.id,interaction.user.id,self.account,group)
                if not result['still_has_access']:
                    channel=guild.get_channel(int(result['channel_id']))
                    if isinstance(channel,discord.TextChannel): await channel.set_permissions(interaction.user,overwrite=None,reason=f'退出網路群組 #{group}')
                await inform(interaction,'✅ 已退出群組')
        except (ValueError,PermissionError,LookupError,discord.HTTPException) as e: await inform(interaction,f'⚠️ {e}')
        except Exception: LOG.exception('群組變更失敗'); await inform(interaction,'⚠️ 系統錯誤')

class GroupsView(PrivateView):
    def __init__(self,owner,character,account): super().__init__(owner); self.character=character; self.account=account
    @discord.ui.button(label='成立群組',emoji='➕',row=0)
    async def create(self,interaction,button): await interaction.response.send_modal(CreateGroupModal(self.account))
    @discord.ui.button(label='我加入的群組',emoji='📋',row=0)
    async def joined(self,interaction,button):
        data=await asyncio.to_thread(ng.memberships,interaction.guild.id,interaction.user.id,self.account)
        s=ng.settings(interaction.guild.id)
        lines=[f'🌐 全員大群：<#{s["global_channel_id"]}>' if s['global_channel_id'] else '🌐 全員大群：尚未綁定']
        lines += [f"#{r['id']} {r['name']} {'✅' if r['state']=='joined' else '✉️ 邀請中'} <#{r['channel_id']}>" for r in data]
        await inform(interaction,'\n'.join(lines)[:1900])
    @discord.ui.button(label='邀請成員',emoji='✉️',row=0)
    async def invite(self,interaction,button): await interaction.response.send_modal(InviteGroupModal(self.account))
    @discord.ui.button(label='接受邀請',emoji='✅',row=1)
    async def accept(self,interaction,button): await interaction.response.send_modal(GroupActionModal(self.account,'accept'))
    @discord.ui.button(label='拒絕邀請',emoji='❌',row=1)
    async def decline(self,interaction,button): await interaction.response.send_modal(GroupActionModal(self.account,'decline'))
    @discord.ui.button(label='退出群組',emoji='🚪',row=1)
    async def leave(self,interaction,button): await interaction.response.send_modal(GroupActionModal(self.account,'leave'))
    @discord.ui.button(label='頻道綁定（群主）',emoji='⚙️',row=3)
    async def bindings(self,interaction,button):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await inform(interaction,'⛔ 僅群主可以開啟頻道綁定設定。')
            return
        await interaction.response.send_message(
            binding_status(interaction.guild_id),
            view=ChannelBindingsView(interaction.user.id), ephemeral=True)

    @discord.ui.button(label='全員大群',emoji='🌐',row=2)
    async def general(self,interaction,button):
        s=ng.settings(interaction.guild.id)
        await inform(interaction,f'🌐 全員大群：<#{s["global_channel_id"]}>' if s['global_channel_id'] else '尚未綁定全員大群頻道')


class ChannelBindingModal(discord.ui.Modal, title='頻道綁定設定'):
    channel_id = discord.ui.TextInput(label='Discord 頻道 ID', placeholder='貼上數字 ID', min_length=15, max_length=22)

    def __init__(self, kind: str, label: str):
        super().__init__()
        self.kind = kind
        self.title = f'設定：{label}'

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await inform(interaction, '⛔ 只有群主可以修改頻道綁定。')
            return
        try:
            cid = int(str(self.channel_id).strip())
            channel = interaction.guild.get_channel(cid)
            if self.kind in ('network_category', 'shop_category'):
                valid = isinstance(channel, discord.CategoryChannel)
                expected = '分類'
            else:
                valid = isinstance(channel, discord.TextChannel)
                expected = '文字頻道'
            if not valid:
                raise ValueError(f'指定 ID 不是本伺服器的{expected}，或機器人尚未取得頻道資料。')
            if self.kind == 'network_category':
                await asyncio.to_thread(ng.set_settings, interaction.guild_id, category_id=cid)
            elif self.kind == 'network_global':
                await asyncio.to_thread(ng.set_settings, interaction.guild_id, global_channel_id=cid)
            elif self.kind == 'shop_category':
                from modules.player_shop_channels import set_category
                await asyncio.to_thread(set_category, interaction.guild_id, cid)
            elif self.kind.startswith('faction:'):
                await asyncio.to_thread(ng.set_settings, interaction.guild_id,
                    faction_name=self.kind.split(':', 1)[1], faction_channel_id=cid)
            else:
                raise ValueError('未知設定類型')
            await inform(interaction, f'✅ 已儲存 {self.title}：{channel.mention}（{cid}）')
        except (ValueError, TypeError) as exc:
            await inform(interaction, f'⚠️ {exc}')
        except Exception:
            LOG.exception('頻道設定更新失敗')
            await inform(interaction, '⚠️ 設定儲存失敗，請查看機器人日誌。')


class ChannelBindingsView(PrivateView):
    """Owner-only configuration; entries are retained in SQLite across restarts."""
    def __init__(self, owner_id):
        super().__init__(owner_id)
        options = [
            discord.SelectOption(label='網路群組分類', value='network_category'),
            discord.SelectOption(label='網路全員大群', value='network_global'),
            discord.SelectOption(label='商業經貿分類', value='shop_category'),
        ]
        options.extend(discord.SelectOption(label=f'預設群組：{name}'[:100], value=f'faction:{name}')
                       for name in ng.DEFAULT_FACTIONS)
        select = discord.ui.Select(placeholder='選擇要修改的頻道綁定', options=options)
        async def selected(interaction):
            value = select.values[0]
            label = next(x.label for x in options if x.value == value)
            await interaction.response.send_modal(ChannelBindingModal(value, label))
        select.callback = selected
        self.add_item(select)

    async def interaction_check(self, interaction):
        if not await super().interaction_check(interaction):
            return False
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await inform(interaction, '⛔ 僅群主可修改頻道設定。')
            return False
        return True


def binding_status(guild_id):
    from modules.player_shop_channels import category_for
    s = ng.settings(guild_id)
    def show(value):
        return f'<#{value}>' if value else '尚未設定'
    rows = [f"網路群組分類：{show(s['category_id'])}",
            f"全員大群：{show(s['global_channel_id'])}",
            f"商業經貿分類：{show(category_for(guild_id))}"]
    rows.extend(f'{name}：{show(cid)}' for name, cid in s['factions'].items())
    return '⚙️ **群主頻道綁定設定**\n' + '\n'.join(rows)
