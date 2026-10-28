"""群主：個人攻略劇情回目編輯 Discord UI。
由 modules.story_builder.StoryBuilderView 開啟，不建立額外斜線指令。
"""
from __future__ import annotations

import json
import sqlite3
import discord
from core.database import get_connection
from modules.story_episodes import init_db, set_episode, configure_reward, get_episodes, rewards_parse


def allowed(i):
    return bool(i.guild and i.guild.owner_id == i.user.id)


def eligible_stories(gid):
    with get_connection() as db:
        return [dict(r) for r in db.execute('''
            SELECT s.id,s.title,s.status,c.name AS character_name
            FROM rp_stories s JOIN characters c
            ON c.guild_id=s.guild_id AND c.id=s.character_id
            WHERE s.guild_id=? AND s.kind='side' AND c.character_type='official_npc'
            ORDER BY s.id DESC LIMIT 25
        ''',(str(gid),)).fetchall()]


def episode_list(gid,sid):
    with get_connection() as db:
        if not db.execute('SELECT 1 FROM rp_stories WHERE guild_id=? AND id=?',(str(gid),sid)).fetchone():
            raise ValueError('找不到這篇劇情。')
        return get_episodes(db,sid)


def screen(gid,sid,number=None):
    with get_connection() as db:
        story=db.execute('SELECT title,status FROM rp_stories WHERE guild_id=? AND id=?',(str(gid),sid)).fetchone()
    if not story:raise ValueError('劇情不存在。')
    episodes=episode_list(gid,sid)
    lines=[f'📖 **{discord.utils.escape_mentions(story["title"])}**｜劇情 #{sid}',f'狀態：{story["status"]}', '', '**本章回目**']
    for e in episodes:
        lines.append(f'第 {e["episode_no"]} 回｜{discord.utils.escape_mentions(e["title"])}｜段落 {e["start_segment"]} 起')
    if not episodes:lines.append('尚無回目。先建立第一回（起始段落 1）。')
    if number is not None:
        chosen=next((e for e in episodes if e['episode_no']==number),None)
        if chosen:
            lines.extend(['',f'**第 {number} 回獎勵**'])
            for r in rewards_parse(chosen['rewards_json']):
                names={'coins':'金幣','item':'背包商品','gift':'角色綁定專屬禮物','stat':'角色數值'}
                lines.append(f'• {names[r["kind"]]}：{r["value"]} ×{r.get("quantity",1)}')
            if not rewards_parse(chosen['rewards_json']):lines.append('尚未設定獎勵。')
    lines.extend(['','※ 一回可以包含多個段落；完成一回才發獎。','※ 專屬禮物綁定獲獎角色，不可跨角色使用或轉贈。'])
    return '\n'.join(lines)[:1900]


class NewEpisodeModal(discord.ui.Modal,title='新增／修改劇情回目'):
    no=discord.ui.TextInput(label='回目編號',placeholder='例如 1',max_length=3)
    title_field=discord.ui.TextInput(label='回目標題',max_length=100)
    start=discord.ui.TextInput(label='起始段落編號',placeholder='第 1 回填 1',max_length=3)
    def __init__(self,sid):super().__init__();self.sid=sid
    async def on_submit(self,i):
        if not allowed(i):return await i.response.send_message('⛔ 僅群主可操作。',ephemeral=True)
        try:
            set_episode(i.guild_id,self.sid,int(self.no.value),str(self.title_field.value),int(self.start.value))
            msg=f'✅ 已保存第 {self.no.value} 回。回目必須先有對應的起始段落。'
        except (ValueError,sqlite3.Error) as exc:msg=f'⚠️ {exc}'
        await i.response.send_message(msg,ephemeral=True)


class RewardModal(discord.ui.Modal,title='新增本回通關獎勵'):
    kind=discord.ui.TextInput(label='獎勵類型：金幣／商品／禮物／數值',max_length=5,placeholder='禮物')
    value=discord.ui.TextInput(label='內容：金幣數量／商品 ID／禮物名／屬性名',max_length=80)
    quantity=discord.ui.TextInput(label='數量（預設 1）',required=False,max_length=4,placeholder='1')
    def __init__(self,sid,no):super().__init__();self.sid=sid;self.no=no
    async def on_submit(self,i):
        if not allowed(i):return await i.response.send_message('⛔ 僅群主可操作。',ephemeral=True)
        try:
            kind={'金幣':'coins','商品':'item','禮物':'gift','數值':'stat'}.get(str(self.kind.value).strip())
            if not kind:raise ValueError('類型只能填金幣、商品、禮物、數值。')
            amount=int(self.quantity.value) if str(self.quantity.value or '').strip() else 1
            rewards=configure_reward(i.guild_id,self.sid,self.no,kind,str(self.value.value).strip(),amount)
            msg=f'✅ 第 {self.no} 回新增獎勵成功，現有 {len(rewards)} 項。專屬禮物只供獲獎角色使用。'
        except (ValueError,sqlite3.Error) as exc:msg=f'⚠️ {exc}'
        await i.response.send_message(msg,ephemeral=True)


class StorySelect(discord.ui.Select):
    def __init__(self,gid):
        rows=eligible_stories(gid)
        options=[discord.SelectOption(label=f'#{r["id"]} {r["title"]}'[:100],description=f'{r["character_name"]}｜{r["status"]}'[:100],value=str(r['id'])) for r in rows]
        if not options:options=[discord.SelectOption(label='沒有官方角色個人支線',value='none')]
        super().__init__(placeholder='先選擇個人攻略劇情',options=options,disabled=not bool(rows),row=0)
    async def callback(self,i):
        sid=int(self.values[0]);v=EpisodeView(i,sid)
        await i.response.edit_message(content=screen(i.guild_id,sid),view=v)


class PickStoryView(discord.ui.View):
    def __init__(self,i):
        super().__init__(timeout=600);self.uid=i.user.id;self.gid=i.guild_id;self.add_item(StorySelect(i.guild_id))
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not allowed(i):
            await i.response.send_message('⛔ 沒有權限使用這個面板。',ephemeral=True);return False
        return True
    @discord.ui.button(label='⬅️ 返回劇情編輯器',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,i,b):
        from modules.story_builder import StoryBuilderView
        await i.response.edit_message(content='📖 **角色劇情編輯器**',view=StoryBuilderView(i))


class EpisodeSelect(discord.ui.Select):
    def __init__(self,gid,sid):
        rows=episode_list(gid,sid)
        opts=[discord.SelectOption(label=f'第 {r["episode_no"]} 回｜{r["title"]}'[:100],value=str(r['episode_no'])) for r in rows]
        if not opts:opts=[discord.SelectOption(label='請先新增回目',value='none')]
        super().__init__(placeholder='選擇回目查看與設定獎勵',options=opts,disabled=not bool(rows),row=0)
    async def callback(self,i):
        self.view.no=int(self.values[0]);sid=self.view.sid
        await i.response.edit_message(content=screen(i.guild_id,sid,self.view.no),view=EpisodeView(i,sid,self.view.no))


class EpisodeView(discord.ui.View):
    def __init__(self,i,sid,no=None):
        super().__init__(timeout=600);self.sid=sid;self.no=no;self.uid=i.user.id;self.gid=i.guild_id
        self.add_item(EpisodeSelect(i.guild_id,sid))
        self.add_reward.disabled=no is None
    async def interaction_check(self,i):
        if i.user.id!=self.uid or i.guild_id!=self.gid or not allowed(i):
            await i.response.send_message('⛔ 沒有權限使用這個面板。',ephemeral=True);return False
        return True
    @discord.ui.button(label='➕ 新增／修改回目',style=discord.ButtonStyle.success,row=1)
    async def new_episode(self,i,b):await i.response.send_modal(NewEpisodeModal(self.sid))
    @discord.ui.button(label='🎁 新增本回獎勵',style=discord.ButtonStyle.primary,row=1)
    async def add_reward(self,i,b):await i.response.send_modal(RewardModal(self.sid,self.no))
    @discord.ui.button(label='🔄 重新整理',style=discord.ButtonStyle.secondary,row=2)
    async def refresh(self,i,b):await i.response.edit_message(content=screen(i.guild_id,self.sid,self.no),view=EpisodeView(i,self.sid,self.no))
    @discord.ui.button(label='⬅️ 返回劇情清單',style=discord.ButtonStyle.secondary,row=2)
    async def back(self,i,b):await i.response.edit_message(content='選擇要編輯回目的個人攻略劇情：',view=PickStoryView(i))


class EpisodeManageButton(discord.ui.Button):
    def __init__(self):super().__init__(label='📚 回目與獎勵',style=discord.ButtonStyle.success,row=2)
    async def callback(self,i):
        if not allowed(i):return await i.response.send_message('⛔ 僅群主可操作。',ephemeral=True)
        init_db()
        await i.response.edit_message(content='選擇要編輯回目的個人攻略劇情：',view=PickStoryView(i))

# ==========================================
# Discord 擴充模組載入入口
# ==========================================

async def setup(bot):
    """
    此模組提供劇情回目與獎勵管理介面。

    介面由 story_builder.py 呼叫，
    因此不需要重複註冊 Cog 或斜線指令。

    保留此 setup 函式，讓 main.py
    能正常透過 bot.load_extension() 載入。
    """
    import logging

    logger = logging.getLogger(__name__)

    logger.info(
        "劇情回目與獎勵管理介面已載入。"
    )
