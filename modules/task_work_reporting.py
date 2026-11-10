"""通告工作天數回報介面：短文公開送出／長文從本人帖子驗證後計算。"""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone
import discord

from core.database import get_connection
from core.work_duration import assignment, count_characters, submit_source


def _expected_title(name, task):
    return re.sub(r'\s+', '', str(name) + str(task))


def _matching_title(title, name, task):
    base=_expected_title(name,task)
    actual=re.sub(r'\s+', '', str(title))
    if actual==base:return True
    if not actual.startswith(base):return False
    tail=actual[len(base):]
    return bool(re.fullmatch(r'[-_（(]?[1-9][0-9]{0,2}[）)]?',tail))


def _correct_context(interaction,cid,task,*,long_form=False):
    """短文必須在指定工作頻道；長文必須在自己的角色論壇帖子。"""
    from modules.z_tasks_center import _thread_owned_characters
    own_forum=cid in {int(c['id']) for c in _thread_owned_characters(interaction)}
    settings=json.loads(task.get('report_json') or '{}')
    required=str(settings.get('channel_id') or '')
    if long_form:
        return own_forum
    if required:
        return str(interaction.channel_id)==required
    return own_forum


def _read_assignment_context(gid,cid,task_id):
    current=assignment(gid,cid,task_id)
    if current is None:return None
    with get_connection() as db:
        char=db.execute('SELECT name,owner_id,status FROM characters WHERE guild_id=? AND id=?',(str(gid),int(cid))).fetchone()
        task=db.execute('SELECT title,report_json FROM rp_task_definitions WHERE guild_id=? AND id=?',(str(gid),int(task_id))).fetchone()
        if not char or not task:raise ValueError('角色或通告不存在')
        return current,dict(char),dict(task)


class ShortWorkModal(discord.ui.Modal,title='通告短文回報（最多100字）'):
    content=discord.ui.TextInput(label='通告正文（每10字折抵1天）',style=discord.TextStyle.paragraph,
                                  min_length=10,max_length=100,required=True)

    def __init__(self,uid,gid,cid,tid):
        super().__init__()
        self.uid,self.gid,self.cid,self.tid=str(uid),str(gid),int(cid),int(tid)

    async def on_submit(self,interaction):
        if str(interaction.user.id)!=self.uid or str(interaction.guild_id)!=self.gid:
            return await interaction.response.send_message('只有原填寫者可以提交。',ephemeral=False)
        text=str(self.content).strip()
        if count_characters(text)<10:
            return await interaction.response.send_message('有效正文不足10字。',ephemeral=False)
        context=_read_assignment_context(self.gid,self.cid,self.tid)
        if context is None:
            return await interaction.response.send_message('這份通告未設定天數，仍沿用原先的審核回報方式。',ephemeral=False)
        duration,char,task=context
        if char['status']!='active' or str(char['owner_id'])!=self.uid:
            return await interaction.response.send_message('角色未啟用或不是本人。',ephemeral=False)
        # Modal cannot switch channel: must remain in the original selected authorized context.
        # Recheck work channel membership at submission to prevent copied modal IDs.
        if not _correct_context(interaction,self.cid,task):
            return await interaction.response.send_message('短文請在通告指定的工作頻道回報。',ephemeral=False)
        await interaction.response.defer(ephemeral=False,thinking=True)
        # 發出可供所有人查看的 RP 正文，保留 Discord 訊息 ID 作唯一計算依據。
        try:
            posted=await interaction.followup.send(
                f'📝 **{char["name"]}｜{task["title"]}**\n{text}',
                ephemeral=False,wait=True,
                allowed_mentions=discord.AllowedMentions.none())
            result=submit_source(self.gid,self.cid,self.tid,f'discord:{posted.id}',text)
        except (ValueError,discord.HTTPException) as exc:
            return await interaction.followup.send(f'回報失敗：{exc}',ephemeral=False)
        await interaction.followup.send(_result_message(result),ephemeral=False)


def _result_message(result):
    line=(f'📅 通告進度：{result["completed_days"]}/{result["required_days"]} 天；'
          f'本次新增 {result["credited_days"]} 天；'
          f'本期工作額度剩餘 {result["budget_remaining"]} 天。')
    if result['newly_completed']:
        line+='\n✅ 工作天數已達標，已進入自動獎勵結算。'
        if result.get('reward_error'):line+='\n⚠️ 獎勵待補發，請勿重複發文。'
    elif result['remaining_days']:
        line+=f'\n仍需完成 {result["remaining_days"]} 天，可分段繼續回報。'
    if result['duplicate']:line+='\n（已計算過的內容不會重複加天數）'
    return line


async def _long_thread_messages(interaction,character_name,task_title,accepted_at):
    channel=interaction.channel
    if not isinstance(channel,discord.Thread) or not _matching_title(channel.name,character_name,task_title):
        raise ValueError('長文回報請使用「角色名字＋通告名稱」，續篇加 1、2、3 的帖子標題。')
    from modules.task_evidence import validate_evidence
    found=[]
    try:
        async for msg in channel.history(limit=300,oldest_first=True):
            if msg.author.id != interaction.user.id or msg.author.bot:continue
            if not msg.content or msg.content.startswith('/'):continue
            item={'message_id':str(msg.id),'author_id':str(msg.author.id),'channel_id':str(msg.channel.id),
                  'created_at':msg.created_at,'content':msg.content}
            try:validate_evidence(item,interaction.user.id,interaction.channel_id,accepted_at)
            except ValueError:continue
            found.append(item)
    except (discord.Forbidden,discord.HTTPException) as exc:
        raise ValueError('無法讀取帖子內容，請檢查閱讀歷史訊息權限') from exc
    if sum(count_characters(x['content']) for x in found)<100:
        raise ValueError('本篇有效字數少於100字；短文請使用表單，長文可分篇發表後逐篇回報。')
    return found


async def open_work_report(interaction,character_id,task_id):
    """從已授權的角色／通告選單呼叫；不信任玩家自行填 ID。"""
    context=_read_assignment_context(interaction.guild_id,character_id,task_id)
    if context is None:return False
    duration,char,task=context
    if char['status']!='active' or str(char['owner_id'])!=str(interaction.user.id):
        raise ValueError('只能以自己的已啟用角色回報')
    channel=interaction.channel
    in_long_thread=(isinstance(channel,discord.Thread)
        and _matching_title(channel.name,char['name'],task['title']))
    if not _correct_context(interaction,int(character_id),task,long_form=in_long_thread):
        raise ValueError('短文請到指定工作頻道；長文請在本人角色論壇內，以角色名及通告名命名帖子。')
    if in_long_thread:
        with get_connection() as db:
            row=db.execute('''SELECT accepted_at FROM rp_task_progress WHERE guild_id=? AND character_id=? AND task_id=?
                AND status IN ('accepted','completed','claimed') ORDER BY accepted_at DESC LIMIT 1''',
                (str(interaction.guild_id),int(character_id),int(task_id))).fetchone()
        if not row:raise ValueError('找不到正式接取紀錄')
        sources=await _long_thread_messages(interaction,char['name'],task['title'],row['accepted_at'])
        await interaction.response.defer(ephemeral=False,thinking=True)
        result=None
        for item in sources:
            result=submit_source(interaction.guild_id,character_id,task_id,
                 f'discord:{item["message_id"]}',item['content'])
        await interaction.followup.send(_result_message(result),ephemeral=False)
    else:
        await interaction.response.send_modal(ShortWorkModal(interaction.user.id,interaction.guild_id,character_id,task_id))
    return True
