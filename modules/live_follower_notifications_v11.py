"""開播後投遞至關注者的既有角色論壇通知帖子；不另開 DM。"""
from __future__ import annotations
import asyncio
import logging
import discord
from core.database import transaction
from core import live_rankings_v11 as rank
LOG=logging.getLogger(__name__)

def _seen(gid,sid,follower):
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS rp_live_follower_notice_receipts (
            guild_id TEXT NOT NULL,session_id INTEGER NOT NULL,character_id INTEGER NOT NULL,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,session_id,character_id))''')
        return db.execute('SELECT 1 FROM rp_live_follower_notice_receipts WHERE guild_id=? AND session_id=? AND character_id=?',
                          (str(gid),int(sid),int(follower))).fetchone() is not None

def _mark(gid,sid,follower):
    with transaction() as db:
        db.execute('''INSERT OR IGNORE INTO rp_live_follower_notice_receipts
            (guild_id,session_id,character_id) VALUES (?,?,?)''',(str(gid),int(sid),int(follower)))

async def notify_live_followers(guild,session_id,host_character_id,title,kind):
    if guild is None:return {'sent':0,'missing':0,'failed':0}
    from modules.character_forums import get_link
    people=await asyncio.to_thread(rank.followers,guild.id,host_character_id)
    stats={'sent':0,'missing':0,'failed':0}
    for person in people:
        cid=int(person['follower_character_id'])
        try:
            if await asyncio.to_thread(_seen,guild.id,session_id,cid):continue
            link=await asyncio.to_thread(get_link,guild.id,cid)
            notice_id=link.get('notice_id')
            if not notice_id:
                stats['missing']+=1;continue
            channel=guild.get_thread(int(notice_id))
            if channel is None:channel=await guild.fetch_channel(int(notice_id))
            await channel.send(
                f'🔔 **你關注的直播間開播了！**\n🎙️ {discord.utils.escape_markdown(str(title)[:80])}\n'
                f'類別：{kind}｜直播間主持角色 #{host_character_id}\n'
                '請使用 `/網路 → 直播中心 → 查看直播` 進入。',
                allowed_mentions=discord.AllowedMentions.none())
            await asyncio.to_thread(_mark,guild.id,session_id,cid)
            stats['sent']+=1
        except (discord.HTTPException,PermissionError,LookupError,TypeError,ValueError):
            LOG.exception('開播提醒失敗 guild=%s follower=%s',guild.id,cid)
            stats['failed']+=1
    return stats
