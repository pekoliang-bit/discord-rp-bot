"""Discord RP｜問題通報表情獎勵（不新增斜線指令）。

僅監看指定頻道；只接受群主的 ‼️ / 🆗，且由通報原作者領獎。
獎金給原作者名下餘額最低的 active 角色，每個階段只結算一次。
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from core.database import get_connection, transaction, account_balance, get_or_create_account
from core.economy import _payment

LOG = logging.getLogger(__name__)
REPORT_CHANNEL_ID = 1571884887248601221
ACK_EMOJI = "‼️"
DONE_EMOJI = "🆗"
BETA_ROLE_NAME = "內測玩家"


def init_db():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_bug_report_rewards (
            guild_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            author_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            operator_id TEXT NOT NULL,
            rewarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, message_id, stage)
        )""")


def _least_wealthy_character(db, guild_id, author_id):
    rows = db.execute("""
        SELECT c.id, c.name, a.id AS wallet_id
        FROM characters AS c
        LEFT JOIN accounts AS a
            ON a.guild_id=c.guild_id AND a.owner_type='character'
           AND a.owner_id=CAST(c.id AS TEXT) AND a.account_type='wallet'
        WHERE c.guild_id=? AND c.owner_id=? AND c.status='active'
          AND c.character_type='player'
        ORDER BY c.id
    """, (str(guild_id), str(author_id))).fetchall()
    if not rows:
        return None
    return min(rows, key=lambda r: (
        account_balance(db, guild_id, r['wallet_id']) if r['wallet_id'] is not None else 0,
        r['id'],
    ))


def reward_once(guild_id, message_id, author_id, operator_id, stage, is_beta):
    """原子入帳。重複表情、重啟與併發不會重複領同一階段。"""
    if stage not in ('ack', 'done'):
        raise ValueError('未知通報狀態')
    guild_id, message_id = str(guild_id), str(message_id)
    with transaction() as db:
        existing = db.execute(
            'SELECT 1 FROM rp_bug_report_rewards WHERE guild_id=? AND message_id=? AND stage=?',
            (guild_id, message_id, stage)
        ).fetchone()
        if existing:
            return None
        if stage == 'done':
            ack = db.execute(
                "SELECT 1 FROM rp_bug_report_rewards WHERE guild_id=? AND message_id=? AND stage='ack'",
                (guild_id, message_id)
            ).fetchone()
            if not ack:
                raise ValueError('請先用 ‼️ 標記已收到，再用 🆗 結案。')
        target = _least_wealthy_character(db, guild_id, author_id)
        if target is None:
            raise ValueError('通報者尚無啟用的原創角色，無法直接入帳。')
        amount = 1000 if stage == 'done' else (1000 if is_beta else 500)
        wallet = target['wallet_id'] or get_or_create_account(
            db, guild_id, 'character', str(target['id']), 'wallet'
        )
        treasury = get_or_create_account(
            db, guild_id, 'system', 'bug_report_rewards', 'treasury'
        )
        _payment(
            db, guild_id, f'bug-report:{message_id}:{stage}',
            treasury, wallet, amount,
            description=f'問題通報 {stage}｜訊息 {message_id}',
        )
        db.execute("""
            INSERT INTO rp_bug_report_rewards
            (guild_id,message_id,stage,author_id,character_id,amount,operator_id)
            VALUES (?,?,?,?,?,?,?)
        """, (guild_id, message_id, stage, str(author_id),
              int(target['id']), amount, str(operator_id)))
        return dict(stage=stage, character_id=int(target['id']),
                    character_name=target['name'], amount=amount)


class BugReportRewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        await asyncio.to_thread(init_db)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.channel_id != REPORT_CHANNEL_ID:
            return
        emoji = str(payload.emoji)
        stage = 'ack' if emoji == ACK_EMOJI else ('done' if emoji == DONE_EMOJI else None)
        if stage is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or payload.user_id != guild.owner_id:
            return  # 指定「我」的操作：僅伺服器群主能發放獎金
        channel = guild.get_channel(payload.channel_id) or self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except discord.HTTPException:
                LOG.warning('問題通報頻道不可讀取：%s', payload.channel_id)
                return
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.bot or message.author.id == payload.user_id:
                return
            # 要求有文字情況說明以及截圖；不把一般聊天誤判為通報。
            if not message.content.strip() or not any(
                a.content_type and a.content_type.startswith('image/')
                or a.filename.lower().endswith(('.png','.jpg','.jpeg','.webp','.gif'))
                for a in message.attachments
            ):
                LOG.info('略過未附截圖或情況說明的通報訊息 %s', message.id)
                return
            member = guild.get_member(message.author.id)
            if member is None:
                try:
                    member = await guild.fetch_member(message.author.id)
                except discord.HTTPException:
                    LOG.warning('通報者已不在伺服器：%s', message.author.id)
                    return
            beta = any(role.name == BETA_ROLE_NAME for role in member.roles)
            result = await asyncio.to_thread(
                reward_once, guild.id, message.id, message.author.id,
                payload.user_id, stage, beta,
            )
            if result is not None:
                label = '已收到，正在處理' if stage == 'ack' else '已處理完成'
                await message.reply(
                    f'✅ {label}。感謝 <@{message.author.id}> 回報！\n'
                    f'獎勵：**{result["amount"]:,} 金幣** → '
                    f'角色 **#{result["character_id"]} {result["character_name"]}**',
                    mention_author=False,
                )
        except ValueError as exc:
            LOG.warning('通報獎勵略過（訊息 %s）：%s', payload.message_id, exc)
        except (discord.HTTPException, discord.Forbidden):
            LOG.exception('通報訊息讀取或回覆失敗')
        except Exception:
            LOG.exception('問題通報獎勵結算失敗，訊息 %s', payload.message_id)


async def setup(bot):
    await bot.add_cog(BugReportRewards(bot))
