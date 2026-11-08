"""/網路 的公開論壇貼文、關注與好友管理，不新增 Slash 指令。"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse
import re
from core.network_post_categories import classify_forum_tags
import discord
from core import network_groups as ng
from core import network_social_posts as social

LOG=logging.getLogger(__name__)


class OwnerView(discord.ui.View):
    def __init__(self,user_id,account_id,timeout=300):
        super().__init__(timeout=timeout)
        self.owner_id=int(user_id)
        self.account_id=int(account_id)

    async def interaction_check(self,interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('請使用自己的網路面板。',ephemeral=True)
            return False
        return True


class ForumBindingModal(discord.ui.Modal,title='綁定公開網路論壇'):
    forum_id=discord.ui.TextInput(label='論壇頻道 ID 或 Discord 連結',placeholder='https://discord.com/channels/伺服器ID/論壇ID')
    async def on_submit(self,interaction):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message('只有群主可以綁定論壇。',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            raw=str(self.forum_id).strip()
            match=re.fullmatch(r'https?://(?:www\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/?',raw)
            if match:
                if int(match.group(1))!=interaction.guild.id:
                    raise ValueError('連結不是本伺服器的頻道')
                forum_id=int(match.group(2))
            elif raw.isdigit():
                forum_id=int(raw)
            else:
                raise ValueError('請填入有效 Discord 論壇連結或頻道 ID')
            forum=interaction.guild.get_channel(forum_id)
            if forum is None:
                forum=await interaction.guild.fetch_channel(forum_id)
            if not isinstance(forum,discord.ForumChannel):
                raise ValueError('這個 ID 不是本伺服器可存取的論壇頻道')
            await asyncio.to_thread(social.bind_forum,interaction.guild.id,forum.id)
            await interaction.followup.send(f'✅ 已綁定 {forum.mention}；發文時會讀取該論壇現有標籤。',ephemeral=True)
        except (ValueError,TypeError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('網路論壇綁定失敗')
            await interaction.followup.send('⚠️ 綁定失敗，請查看 Railway 日誌。',ephemeral=True)


class PostModal(discord.ui.Modal,title='發布網路論壇文章'):
    title_input=discord.ui.TextInput(label='文章標題',max_length=100,min_length=1)
    body_input=discord.ui.TextInput(label='文章內容',style=discord.TextStyle.paragraph,max_length=1400,min_length=1)
    photo=discord.ui.TextInput(label='照片連結（選填）',required=False,max_length=400)

    def __init__(self,account_id,forum_id,tag_ids):
        super().__init__()
        self.account_id=int(account_id)
        self.forum_id=int(forum_id)
        self.tag_ids=list(tag_ids)

    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True,thinking=True)
        thread=None
        try:
            account=await asyncio.to_thread(ng.account_info,interaction.guild.id,
                                            interaction.user.id,self.account_id)
            forum=interaction.guild.get_channel(self.forum_id)
            if not isinstance(forum,discord.ForumChannel):
                raise ValueError('綁定的論壇頻道已不存在，請通知群主重新綁定')
            url=str(self.photo.value or '').strip()
            if url:
                parsed=urlparse(url)
                if parsed.scheme not in ('https','http') or not parsed.netloc or parsed.username or parsed.password:
                    raise ValueError('照片需要填寫公開的 http(s) 網址')
                if len(url)>400:
                    raise ValueError('照片網址過長')
            # Verify tags against current forum tags; don't allow arbitrary/other-forum tags.
            tag_map={tag.id:tag for tag in forum.available_tags}
            if any(tag_id not in tag_map for tag_id in self.tag_ids):
                raise ValueError('論壇標籤已變更，請重新選擇')
            text=str(self.body_input.value).strip()
            text+='\n\n' + (f'🖼️ {url}\n' if url else '')
            text+=f"\n發布帳號：#{account['handle']} ｜角色編號：#{account['character_id']}"
            created=await forum.create_thread(name=str(self.title_input.value),content=text,
                applied_tags=[tag_map[i] for i in self.tag_ids],
                allowed_mentions=discord.AllowedMentions.none(),
                reason=f'網路發文：角色 {account["character_id"]}')
            thread=created.thread
            try:
                await asyncio.to_thread(social.save_post,interaction.guild.id,
                                        interaction.user.id,self.account_id,thread.id,self.tag_ids)
                categories=classify_forum_tags([tag_map[i].name for i in self.tag_ids])
                LOG.info('網路論壇貼文完成 thread=%s categories=%s',thread.id,categories)
            except Exception:
                LOG.exception('網路發文後儲存索引失敗 thread=%s',thread.id)
                try:
                    await thread.delete(reason='發文資料保存失敗，復原')
                except discord.HTTPException:
                    LOG.exception('刪除未索引網路帖失敗 thread=%s',thread.id)
                raise
            await interaction.followup.send(f'✅ 發文成功：{thread.mention}',ephemeral=True)
        except (PermissionError,LookupError,ValueError,discord.Forbidden) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('論壇發文失敗')
            await interaction.followup.send('⚠️ 發文失敗，請查看 Railway 日誌。',ephemeral=True)


class ForumPostView(OwnerView):
    def __init__(self,user_id,account_id,forum):
        super().__init__(user_id,account_id)
        self.forum_id=forum.id
        self.tag_ids=[]
        tags=forum.available_tags
        if tags:
            sel=discord.ui.Select(placeholder='選擇論壇標籤（可複選）',min_values=1,
                                  max_values=min(5,len(tags)),
                                  options=[discord.SelectOption(label=t.name[:100],value=str(t.id))
                                           for t in tags[:25]],row=0)
            async def select_tags(interaction):
                self.tag_ids=[int(x) for x in sel.values]
                await interaction.response.send_message(
                    '✅ 已選擇：'+', '.join(t.name for t in tags if t.id in self.tag_ids),
                    ephemeral=True)
            sel.callback=select_tags
            self.add_item(sel)

    @discord.ui.button(label='填寫標題與發文',emoji='📝',style=discord.ButtonStyle.primary,row=1)
    async def compose(self,interaction,button):
        await interaction.response.send_modal(PostModal(self.account_id,self.forum_id,self.tag_ids))


class FriendsView(OwnerView):
    def __init__(self,user_id,account_id,pending):
        super().__init__(user_id,account_id)
        self.requester=None
        if pending:
            sel=discord.ui.Select(placeholder='選擇待處理的好友申請',row=0,
                options=[discord.SelectOption(label=f"@{r['handle']}｜角色 #{r['character_id']}"[:100],
                        value=str(r['requester_account_id'])) for r in pending[:25]])
            async def choose(interaction):
                self.requester=int(sel.values[0])
                await interaction.response.send_message('已選取申請，請按同意或拒絕。',ephemeral=True)
            sel.callback=choose
            self.add_item(sel)

    async def decide(self,interaction,accept):
        await interaction.response.defer(ephemeral=True)
        if self.requester is None:
            await interaction.followup.send('請先選擇申請。',ephemeral=True)
            return
        try:
            await asyncio.to_thread(social.decide_friend,interaction.guild.id,
                                    interaction.user.id,self.account_id,self.requester,accept)
            await interaction.followup.send('✅ 已同意好友申請。' if accept else '已拒絕好友申請。',ephemeral=True)
        except (ValueError,PermissionError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)

    @discord.ui.button(label='同意',emoji='✅',style=discord.ButtonStyle.success,row=1)
    async def accept(self,interaction,button):
        await self.decide(interaction,True)

    @discord.ui.button(label='拒絕',emoji='❌',row=1)
    async def decline(self,interaction,button):
        await self.decide(interaction,False)


class ForumActions(OwnerView):
    @discord.ui.button(label='📝 發表文章',style=discord.ButtonStyle.primary,row=0)
    async def post(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(ng.account_info,interaction.guild.id,interaction.user.id,self.account_id)
            forum_id=await asyncio.to_thread(social.bound_forum,interaction.guild.id)
            forum=interaction.guild.get_channel(forum_id) if forum_id else None
            if forum is None and forum_id:
                forum=await interaction.guild.fetch_channel(forum_id)
            if not isinstance(forum,discord.ForumChannel):
                await interaction.followup.send('⚠️ 群主尚未綁定網路論壇，請先使用「論壇綁定」。',ephemeral=True)
                return
            await interaction.followup.send(f'📝 發文至 {forum.mention}；下拉標籤來自此論壇的現有設定。',
                view=ForumPostView(interaction.user.id,self.account_id,forum),ephemeral=True)
        except Exception:
            LOG.exception('無法顯示論壇發文入口')
            await interaction.followup.send('⚠️ 發文入口載入失敗，請查看日誌。',ephemeral=True)

    @discord.ui.button(label='好友申請',emoji='🤝',row=0)
    async def friends(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        try:
            rows=await asyncio.to_thread(social.pending_friends,interaction.guild.id,
                                          interaction.user.id,self.account_id)
            await interaction.followup.send(f'🤝 待處理好友申請：{len(rows)} 件',
                view=FriendsView(interaction.user.id,self.account_id,rows) if rows else None,
                ephemeral=True)
        except Exception:
            LOG.exception('好友申請清單失敗')
            await interaction.followup.send('⚠️ 好友申請暫時無法載入。',ephemeral=True)

    @discord.ui.button(label='論壇綁定（群主）',emoji='⚙️',row=1)
    async def binding(self,interaction,button):
        if interaction.guild and interaction.user.id==interaction.guild.owner_id:
            await interaction.response.send_modal(ForumBindingModal())
        else:
            await interaction.response.send_message('僅群主可以修改論壇綁定。',ephemeral=True)


class SocialActionModal(discord.ui.Modal,title='關注或加好友'):
    target_handle=discord.ui.TextInput(label='對方網路帳號（不用輸入 @）',max_length=80)
    target_character=discord.ui.TextInput(label='對方角色編號',max_length=16)
    def __init__(self,source_account,action):
        super().__init__()
        self.source_account=source_account
        self.action=action

    async def on_submit(self,interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            target=await asyncio.to_thread(social.resolve_identity,interaction.guild.id,
                         str(self.target_handle.value),int(str(self.target_character.value)))
            if not target:
                raise LookupError('找不到同時符合帳號名稱與角色編號的角色')
            if self.action=='follow':
                await asyncio.to_thread(social.follow,interaction.guild.id,
                            interaction.user.id,self.source_account,target['id'])
                await interaction.followup.send('✅ 已關注該帳號。',ephemeral=True)
            else:
                await asyncio.to_thread(social.request_friend,interaction.guild.id,
                            interaction.user.id,self.source_account,target['id'])
                await notify_friend_request(interaction.guild,target,self.source_account)
                await interaction.followup.send('✅ 已發出好友申請，對方可在通知帖子或網路好友中心處理。',ephemeral=True)
        except (ValueError,PermissionError,LookupError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)
        except Exception:
            LOG.exception('關注/好友申請失敗')
            await interaction.followup.send('⚠️ 操作失敗，請查看日誌。',ephemeral=True)


class FriendNotificationView(discord.ui.View):
    def __init__(self,owner_id,target_account,requester_account):
        super().__init__(timeout=86400)
        self.owner_id=int(owner_id)
        self.target_account=int(target_account)
        self.requester_account=int(requester_account)

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('只有收件角色的持有人可以處理好友申請。',ephemeral=True)
            return False
        return True

    async def decide(self,interaction,accept):
        await interaction.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(social.decide_friend,interaction.guild.id,
                interaction.user.id,self.target_account,self.requester_account,accept)
            await interaction.followup.send('✅ 已接受好友申請。' if accept else '已拒絕好友申請。',ephemeral=True)
            for item in self.children:
                item.disabled=True
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                LOG.warning('好友申請通知按鈕狀態無法刷新')
        except (ValueError,PermissionError) as exc:
            await interaction.followup.send(f'⚠️ {exc}',ephemeral=True)

    @discord.ui.button(label='接受好友',style=discord.ButtonStyle.success,emoji='✅')
    async def yes(self,interaction,button):
        await self.decide(interaction,True)

    @discord.ui.button(label='拒絕好友',emoji='❌')
    async def no(self,interaction,button):
        await self.decide(interaction,False)


async def notify_friend_request(guild,target,requester_account):
    """優先送既有角色的私人論壇通知帖；失敗時仍可在網路好友中心處理。"""
    try:
        from modules.character_forums import get_link
        link=await asyncio.to_thread(get_link,guild.id,target['character_id'])
        notice_id=link.get('notice_id')
        if not notice_id:
            LOG.warning('好友通知無私人帖子 guild=%s char=%s',guild.id,target['character_id'])
            return False
        notice=guild.get_thread(int(notice_id))
        if notice is None:
            notice=await guild.fetch_channel(int(notice_id))
        view=FriendNotificationView(target['owner_id'],target['id'],requester_account)
        await notice.send('🤝 你收到一則新的網路好友申請，請選擇接受或拒絕。',
                          view=view,allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.HTTPException,PermissionError,LookupError):
        LOG.exception('好友通知發送失敗 guild=%s',guild.id)
        return False


class SocialMenuView(ForumActions):
    """兼容論壇發文、關注及好友管理；不靠 Message Content Intent 也能使用。"""
    @discord.ui.button(label='關注帳號',emoji='✅',row=2)
    async def follow_account(self,interaction,button):
        await interaction.response.send_modal(SocialActionModal(self.account_id,'follow'))

    @discord.ui.button(label='加好友',emoji='➕',row=2)
    async def friend_account(self,interaction,button):
        await interaction.response.send_modal(SocialActionModal(self.account_id,'friend'))
