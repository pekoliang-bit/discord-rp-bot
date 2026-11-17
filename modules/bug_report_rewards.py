"""問題通報獎勵：身分快照、待領帳本、去重、正式角色啟用後補發。

頻道 1571884887248601221；群主 ‼️ 已收到、🆗 已處理。
由通報發出當下的內測身分決定金額，不受後續身分改變影響。
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from core.database import get_connection, transaction, account_balance, get_or_create_account
from core.economy import _payment

LOG = logging.getLogger(__name__)
REPORT_CHANNEL_ID = 1571884887248601221
ACK_EMOJI, DONE_EMOJI = '‼️', '🆗'
BETA_ROLE_NAME = '內測玩家'


from core.bug_report_core_v2 import (setup as setup_bug_report_tables,record_snapshot,reward_once,settle_pending_for_owner,settle_all_pending)

def _is_report(message):
    return bool(message.content.strip() and any(
        (a.content_type or '').startswith('image/') or
        a.filename.lower().endswith(('.png','.jpg','.jpeg','.webp','.gif'))
        for a in message.attachments))


def _has_beta_role(member):
    return any(r.name==BETA_ROLE_NAME for r in getattr(member,'roles',()))


class BugReportRewards(commands.Cog):
    def __init__(self,bot):
        self.bot=bot
        self.pending_worker.start()

    def cog_unload(self):
        self.pending_worker.cancel()

    async def cog_load(self):
        await asyncio.to_thread(self._init)

    @staticmethod
    def _init():
        with transaction() as db:
            setup_bug_report_tables(db)

    @commands.Cog.listener()
    async def on_message(self,message:discord.Message):
        if message.guild is None or message.channel.id!=REPORT_CHANNEL_ID or message.author.bot or not _is_report(message):return
        member=message.author
        if not isinstance(member,discord.Member):
            try:member=await message.guild.fetch_member(message.author.id)
            except discord.HTTPException:return
        await asyncio.to_thread(record_snapshot,message.guild.id,message.id,message.author.id,_has_beta_role(member))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self,payload:discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.channel_id!=REPORT_CHANNEL_ID:return
        stage='ack' if str(payload.emoji)==ACK_EMOJI else ('done' if str(payload.emoji)==DONE_EMOJI else None)
        if stage is None:return
        guild=self.bot.get_guild(payload.guild_id)
        if guild is None or payload.user_id!=guild.owner_id:return
        try:
            channel=self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            message=await channel.fetch_message(payload.message_id)
            if message.author.bot or message.author.id==payload.user_id or not _is_report(message):return
            member=guild.get_member(message.author.id) or await guild.fetch_member(message.author.id)
            # 舊通報首次處理才補快照；新通報已由 on_message 固定身分。
            result=await asyncio.to_thread(reward_once,guild.id,message.id,message.author.id,
                                           payload.user_id,stage,_has_beta_role(member))
            if result:
                label='已收到，正在處理' if stage=='ack' else '已處理完成'
                target=(f'正式角色 #{result["recipient"]["id"]}' if result['recipient'] else '待領帳本（正式角色啟用後補發）')
                await message.reply(f'✅ {label}。<@{message.author.id}> 獎勵 {result["amount"]:,} 金幣 → {target}',mention_author=False)
        except ValueError as exc:
            LOG.info('問題通報不符合結算條件：%s',exc)
        except Exception:
            LOG.exception('問題通報獎勵結算失敗：%s',payload.message_id)

    @tasks.loop(hours=4)
    async def pending_worker(self):
        try:await asyncio.to_thread(settle_all_pending)
        except Exception:LOG.exception('待領問題通報補發失敗')

    @pending_worker.before_loop
    async def before_pending_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BugReportRewards(bot))
