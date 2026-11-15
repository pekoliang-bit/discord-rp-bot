"""Unified pre-launch channel-binding UI, opened only from /官方.

Admin can inspect and bind before public launch; Discord owner retains launch authority.
The panel changes bindings, never deletes/moves Discord channels, never enables services.
"""
from __future__ import annotations
import asyncio
import logging
import discord
from core.channel_bindings_v1 import SLOT_ROWS,SLOTS,all_bindings,get_binding,save_binding,unbind
from core.prelaunch_gate import is_management_actor

LOG=logging.getLogger(__name__)
GROUPS=list(dict.fromkeys(x[2] for x in SLOT_ROWS))

def allowed(i, uid, gid):
    return i.guild_id==gid and i.user.id==uid and is_management_actor(i)

def _type_ok(channel,expected):
    if expected=='category':return isinstance(channel,discord.CategoryChannel)
    if expected=='forum':return isinstance(channel,discord.ForumChannel)
    if expected=='text_or_forum':return isinstance(channel,(discord.TextChannel,discord.ForumChannel))
    return isinstance(channel,discord.TextChannel)

def _permissions_error(i,ch,key):
    member=i.guild.me
    if member is None:return '機器人不在伺服器成員快取，請稍後重新嘗試'
    if isinstance(ch,discord.CategoryChannel):
        p=ch.permissions_for(member)
        if not (p.view_channel and p.manage_channels):return '需要查看頻道及管理頻道權限，以便日後建立子頻道'
    elif isinstance(ch,discord.ForumChannel):
        p=ch.permissions_for(member)
        if not (p.view_channel and p.send_messages and p.create_public_threads):
            return '需要查看論壇、發送訊息及建立公開討論串權限'
    else:
        p=ch.permissions_for(member)
        if not (p.view_channel and p.send_messages):return '需要查看及發送訊息權限'
    if key in ('confidential_reports','application_inbox','review_workroom','admin_alerts'):
        # Prevent mistakenly configuring a public channel for confidential reports.
        if key=='confidential_reports' and ch.permissions_for(i.guild.default_role).view_channel:
            return '機密舉報不能綁定一般成員可查看的頻道'
    return None

def summary(gid,group=None):
    saved=all_bindings(gid)
    selected=[row for row in SLOT_ROWS if group is None or row[2]==group]
    out=[f'🔗 **頻道綁定中心｜{group or "全系統"}**',
         '此處可在正式開群之前操作；只儲存設定，不開放玩家系統。','']
    for key,name,grp,typ,default in selected:
        cid=saved.get(key)
        out.append(f'{"✅" if cid else "▫️"} {name}：{f"<#{cid}>" if cid else "未設定"}')
    out.extend(['','⚠️ 僅有填寫 ID 不代表 Discord 已授權；請進入用途重新選擇以執行類型／權限檢查。',
                '官方 NPC 五個角色單發布區與網路陣營群為不同用途。'])
    return '\n'.join(out)[:1900]

class Guard(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600)
        self.uid=i.user.id;self.gid=i.guild_id
    async def interaction_check(self,i):
        if not allowed(i,self.uid,self.gid):
            await i.response.send_message('⛔ 只有開啟面板的群主／Discord 管理員可以操作。',ephemeral=True)
            return False
        return True

class GroupSelect(discord.ui.Select):
    def __init__(self):
        options=[discord.SelectOption(label=x[:90],value=x) for x in GROUPS]
        super().__init__(placeholder='選擇頻道綁定分類',options=options,row=0)
    async def callback(self,i):
        group=self.values[0]
        await i.response.edit_message(content=summary(i.guild_id,group),view=SlotList(i,group))

class Home(Guard):
    def __init__(self,i):
        super().__init__(i)
        self.add_item(GroupSelect())
    @discord.ui.button(label='檢查全部已提供頻道',style=discord.ButtonStyle.secondary,row=1)
    async def audit(self,i,button):
        saved=all_bindings(self.gid)
        errors=[];good=0;unset=0
        for key,label,group,kind,default in SLOT_ROWS:
            cid=saved.get(key)
            if not cid:unset+=1;continue
            ch=i.guild.get_channel(int(cid))
            if ch is None:errors.append(f'{label}：找不到頻道（或快取尚未取得）');continue
            if not _type_ok(ch,kind):errors.append(f'{label}：頻道類型不符合 {kind}');continue
            err=_permissions_error(i,ch,key)
            if err:errors.append(f'{label}：{err}')
            else:good+=1
        lines=[f'🔍 **頻道綁定檢查**：符合 {good}、未設定 {unset}、待修正 {len(errors)}',
               '這是 Discord 快取／機器人權限檢查；不會自動修改頻道或啟用玩家系統。']
        lines+=['• '+x for x in errors[:28]]
        await i.response.send_message('\n'.join(lines)[:1950],ephemeral=True)

class SlotSelect(discord.ui.Select):
    def __init__(self,group,gid):
        rows=[x for x in SLOT_ROWS if x[2]==group]
        options=[discord.SelectOption(label=label[:90],value=key,description='已設定' if get_binding(gid,key) else '未設定') for key,label,group,typ,default in rows]
        super().__init__(placeholder='選擇要變更的頻道用途',options=options,row=0)
    async def callback(self,i):
        await i.response.edit_message(content=detail(i.guild_id,self.values[0]),view=SlotDetail(i,self.values[0]))

def detail(gid,key):
    _,label,group,kind,default=SLOTS[key]
    cid=get_binding(gid,key)
    return (f'🔗 **{label}**\n分類：{group}\n需要類型：{kind}\n'
            f'目前：{f"<#{cid}>" if cid else "未設定"}\n'
            '綁定會同步到對應的原系統設定（若原系統支援）；不會刪除現有頻道。')

class SlotList(Guard):
    def __init__(self,i,group):
        super().__init__(i);self.group=group
        self.add_item(SlotSelect(group,self.gid))
    @discord.ui.button(label='◀️ 返回分類',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,button):
        await i.response.edit_message(content=summary(self.gid),view=Home(i))

class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self,key):
        typ=SLOTS[key][3]
        kinds={'category':[discord.ChannelType.category],'forum':[discord.ChannelType.forum],
             'text_or_forum':[discord.ChannelType.text,discord.ChannelType.forum]}
        super().__init__(placeholder='選擇本伺服器的頻道／分類',channel_types=kinds.get(typ,[discord.ChannelType.text]),
                         min_values=1,max_values=1,row=0)
        self.key=key
    async def callback(self,i):
        channel=self.values[0]
        guild=i.guild
        # Resolve actual channel from the guild, not a user-controlled string.
        ch=guild.get_channel(channel.id)
        if ch is None or ch.guild.id!=guild.id:
            return await i.response.send_message('找不到所選的本群頻道。',ephemeral=True)
        if not _type_ok(ch,SLOTS[self.key][3]):
            return await i.response.send_message('頻道種類不符合該用途。',ephemeral=True)
        error=_permissions_error(i,ch,self.key)
        if error:return await i.response.send_message('⚠️ '+error,ephemeral=True)
        await i.response.edit_message(content=detail(i.guild_id,self.key)+f'\n\n確定改為 {ch.mention}？',
                                      view=Confirm(i,self.key,ch.id))

class SlotDetail(Guard):
    def __init__(self,i,key):
        super().__init__(i);self.key=key
        self.add_item(ChannelPicker(key))
    @discord.ui.button(label='解除此用途綁定',style=discord.ButtonStyle.danger,row=1)
    async def remove(self,i,button):
        await i.response.edit_message(content=detail(self.gid,self.key)+'\n\n確定解除設定？頻道本身不會刪除。',
                                      view=RemoveConfirm(i,self.key))
    @discord.ui.button(label='◀️ 返回用途列表',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,button):
        group=SLOTS[self.key][2]
        await i.response.edit_message(content=summary(self.gid,group),view=SlotList(i,group))

class Confirm(Guard):
    def __init__(self,i,key,cid):
        super().__init__(i);self.key=key;self.cid=cid
    @discord.ui.button(label='確認並儲存',style=discord.ButtonStyle.success)
    async def save(self,i,button):
        ch=i.guild.get_channel(self.cid)
        if ch is None or not _type_ok(ch,SLOTS[self.key][3]):
            return await i.response.send_message('頻道已變更，請重新選擇。',ephemeral=True)
        err=_permissions_error(i,ch,self.key)
        if err:return await i.response.send_message('⚠️ '+err,ephemeral=True)
        try:
            await asyncio.to_thread(save_binding,self.gid,self.key,self.cid,i.user.id)
        except Exception:
            LOG.exception('頻道綁定失敗 guild=%s slot=%s',self.gid,self.key)
            return await i.response.send_message('⚠️ 寫入失敗，請查看 Railway 日誌。',ephemeral=True)
        await i.response.edit_message(content='✅ 已儲存。\n'+detail(self.gid,self.key),view=SlotDetail(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):
        await i.response.edit_message(content=detail(self.gid,self.key),view=SlotDetail(i,self.key))

class RemoveConfirm(Guard):
    def __init__(self,i,key):
        super().__init__(i);self.key=key
    @discord.ui.button(label='確認解除',style=discord.ButtonStyle.danger)
    async def remove(self,i,button):
        await asyncio.to_thread(unbind,self.gid,self.key,i.user.id)
        await i.response.edit_message(content='✅ 已解除綁定。\n'+detail(self.gid,self.key),view=SlotDetail(i,self.key))
    @discord.ui.button(label='取消',style=discord.ButtonStyle.secondary)
    async def cancel(self,i,button):
        await i.response.edit_message(content=detail(self.gid,self.key),view=SlotDetail(i,self.key))

class ChannelBindingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='🔗 頻道綁定中心',style=discord.ButtonStyle.primary,row=2)
    async def callback(self,i):
        if not is_management_actor(i):
            return await i.response.send_message('此功能僅限群主與 Discord 管理員。',ephemeral=True)
        await i.response.send_message(summary(i.guild_id),view=Home(i),ephemeral=True)
