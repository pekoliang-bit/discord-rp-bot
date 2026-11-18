"""官方 NPC 專屬 Discord 論壇；沿用正式角色四帖與角色 ID 授權綁定。

不再建立文字頻道；舊文字頻道不自動刪除，避免遺失已發佈內容。
"""
from __future__ import annotations

import asyncio
import logging
import re

import discord
from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
OFFICIAL_GUILD_ID = 1544051203493601380
OFFICIAL_CATEGORY_ID = 1545373529019777025
FACTIONS = ('單打獨鬥', '經紀公司', 'MCN', '另闢蹊徑', '熟人抱團', '抱團取暖')
FIELD = re.compile(r'^\s*(?:[-*]\s*)?(?:歸屬|所屬陣營|陣營|角色陣營|所屬勢力)\s*[：:]\s*(.+?)\s*$', re.M)
_LOCKS: dict[tuple[int,int], asyncio.Lock] = {}


def extract_faction(sheet_text: str) -> str:
    match = FIELD.search(sheet_text or '')
    if not match:
        return '未分配'
    raw = match.group(1).strip()
    for faction in FACTIONS:
        if faction.casefold() in raw.casefold():
            return faction
    return raw[:40] if raw else '未分配'


def channel_title(name: str, faction: str) -> str:
    return f'{name.strip()} {faction.strip()}'[:100]


def _init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_official_npc_channels (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, character_id)
    )''')


async def _fetch_channel(guild, channel_id):
    if not channel_id:
        return None
    result = guild.get_channel(int(channel_id))
    if result is not None:
        return result
    try:
        return await guild.fetch_channel(int(channel_id))
    except (discord.NotFound, discord.Forbidden):
        return None


async def ensure_official_npc_channel(guild: discord.Guild, character_id: int,
                                      name: str, sheet_text: str, source_url: str = '') -> tuple[discord.ForumChannel | None, str]:
    """冪等建論壇＋四帖；舊文字頻道保留供管理員確認後手動刪除。"""
    if guild.id != OFFICIAL_GUILD_ID:
        return None, '此伺服器尚未設定官方角色論壇分類'

    # 與正式玩家共用綁定表／機器人操作授權，但官方角色使用專屬分類。
    from modules.character_forums import (
        initialize, get_link, update_link, marker, POSTS,
        _forum_access_overwrites, _private_forum_is_secure,
        _missing_forum_permissions, _refresh_forum,
    )
    lock = _LOCKS.setdefault((guild.id, int(character_id)), asyncio.Lock())
    async with lock:
        with get_connection() as db:
            row = db.execute('''SELECT id,name,owner_id,status,character_type FROM characters
                                WHERE guild_id=? AND id=?''', (str(guild.id), int(character_id))).fetchone()
        if row is None or row['character_type'] != 'official_npc' or row['status'] == 'deleted':
            return None, '角色不存在、已刪除或不是官方 NPC'
        if row['status'] != 'active':
            return None, '官方 NPC 尚未正式啟用，無法綁定機器人使用帖子'

        category = await _fetch_channel(guild, OFFICIAL_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            return None, '指定 ID 不是 Discord 分類，或沒有讀取權限'
        bot_member = guild.me
        if bot_member is None or not guild.me.guild_permissions.manage_channels:
            return None, '機器人缺少管理頻道權限'
        owner = guild.get_member(int(row['owner_id']))
        if owner is None:
            try:
                owner = await guild.fetch_member(int(row['owner_id']))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None, '無法確認官方角色持有人，拒絕建立未授權論壇'
        overwrites = _forum_access_overwrites(guild, owner, bot_member)
        initialize()
        with get_connection() as db:
            _init(db)
            old = db.execute('''SELECT channel_id FROM rp_official_npc_channels
                                WHERE guild_id=? AND character_id=?''',
                             (str(guild.id), int(character_id))).fetchone()
        links = get_link(guild.id, character_id)
        forum = await _fetch_channel(guild, links.get('forum_id'))
        legacy = await _fetch_channel(guild, old['channel_id']) if old else None
        if isinstance(forum, discord.ForumChannel) and forum.topic != marker(guild.id, character_id):
            # 資料庫綁定若指向別人的論壇，不能直接覆寫別人的頻道。
            LOG.error('官方角色 #%s 論壇綁定 marker 不符：%s', character_id, forum.id)
            forum = None
        if not isinstance(forum, discord.ForumChannel):
            if isinstance(legacy, discord.ForumChannel):
                forum = legacy
            else:
                forum = next((ch for ch in guild.channels if isinstance(ch, discord.ForumChannel)
                              and ch.topic == marker(guild.id, character_id)), None)
        faction = extract_faction(sheet_text)
        title = channel_title(name or row['name'], faction)
        if faction == '未分配' and isinstance(forum, discord.ForumChannel):
            title = forum.name
        created = False
        if not isinstance(forum, discord.ForumChannel):
            if len(category.channels) >= 50:
                return None, '官方角色分類已達 50 個頻道上限'
            try:
                forum = await guild.create_forum(
                    name=title, category=category, overwrites=overwrites,
                    topic=marker(guild.id, character_id),
                    reason=f'官方角色 #{character_id} 正式登記：專屬論壇',
                )
                created = True
            except discord.HTTPException as exc:
                LOG.exception('官方角色 #%s 建立論壇失敗', character_id)
                return None, f'建立官方角色論壇失敗：{exc}'
        # 統一更新分類、名稱與權限；不使用名稱推測 ID 綁定。
        if (forum.category_id != category.id or forum.name != title
                or forum.topic != marker(guild.id, character_id)
                or not _private_forum_is_secure(forum, row['owner_id'], owner=owner, bot_member=bot_member)):
            await forum.edit(name=title, category=category, topic=marker(guild.id, character_id),
                             overwrites=overwrites, reason=f'修復官方角色 #{character_id} 專屬論壇')
        forum = await _refresh_forum(guild, forum)
        if not _private_forum_is_secure(forum, row['owner_id'], owner=owner, bot_member=bot_member):
            return None, '官方角色論壇的私人存取權限驗證失敗'
        missing = _missing_forum_permissions(forum, bot_member, owner)
        if missing:
            return None, '角色論壇權限不足：' + '、'.join(missing)
        update_link(guild.id, character_id, 'forum_id', forum.id)
        with transaction() as db:
            _init(db)
            db.execute('''INSERT INTO rp_official_npc_channels(guild_id,character_id,channel_id)
                          VALUES(?,?,?) ON CONFLICT(guild_id,character_id)
                          DO UPDATE SET channel_id=excluded.channel_id''',
                       (str(guild.id), int(character_id), str(forum.id)))
        # 注意：先登記 forum_id，之後才可安全綁定 /惡意 的機器人使用帖子。
        for code, post_title, content in POSTS:
            col = {'notice':'notice_id','question':'question_id','story':'story_id','bot':'bot_id'}[code]
            links = get_link(guild.id, character_id)
            thread = guild.get_thread(int(links[col])) if links.get(col) else None
            if thread is None and links.get(col):
                thread = await _fetch_channel(guild, links[col])
            if not isinstance(thread, discord.Thread) or thread.parent_id != forum.id:
                thread = next((x for x in forum.threads if x.name == post_title), None)
                if thread is None:
                    async for item in forum.archived_threads(limit=None):
                        if item.name == post_title:
                            thread = item
                            break
            if thread is None:
                body = content
                if code == 'notice':
                    body = f'<@{row["owner_id"]}>\n{content}'
                elif code == 'story':
                    body += '\n\n🎭 官方角色資料：' + discord.utils.escape_mentions(name)
                    body += '\n陣營：' + discord.utils.escape_mentions(faction)
                    if source_url:
                        body += '\n原始角色單：' + source_url
                    if sheet_text:
                        body += '\n\n' + discord.utils.escape_mentions(sheet_text)[:1200]
                result = await forum.create_thread(
                    name=post_title, content=body[:1900],
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    reason=f'官方角色 #{character_id} 固定帖子：{post_title}',
                )
                thread = result.thread
            update_link(guild.id, character_id, col, thread.id)
            if code == 'bot':
                from core.malicious_forum_permissions import register_bot_thread
                register_bot_thread(guild.id, int(character_id), forum.id, thread.id,
                                    setter_id=int(guild.owner_id), owner_authorized=True)
            if code == 'notice' and not bool(getattr(getattr(thread,'flags',None), 'pinned',False)):
                await thread.edit(pinned=True, reason='角色通知帖子置頂')
        note = '已建立官方角色專屬論壇與四個固定帖子' if created else '已確認／修復官方角色論壇與四個固定帖子'
        if isinstance(legacy, discord.TextChannel):
            note += f'；舊文字頻道 <#{legacy.id}> 已保留，請確認後手動刪除'
        return forum, note


async def repair_official_npc_forums(guild: discord.Guild, limit: int = 5, *, force: bool = False):
    """由既有正式論壇背景掃描順帶補建官方 NPC；不碰已刪除角色。"""
    if guild.id != OFFICIAL_GUILD_ID:
        return 0, []
    with get_connection() as db:
        if force:
            rows = db.execute("""SELECT id,name FROM characters
                WHERE guild_id=? AND character_type='official_npc' AND status='active'
                ORDER BY id LIMIT ?""", (str(guild.id), int(limit))).fetchall()
        else:
            rows = db.execute("""SELECT c.id,c.name FROM characters c
                LEFT JOIN character_forum_links f ON f.guild_id=c.guild_id AND f.character_id=c.id
                WHERE c.guild_id=? AND c.character_type='official_npc' AND c.status='active'
                AND (f.forum_id IS NULL OR f.notice_id IS NULL OR f.question_id IS NULL
                     OR f.story_id IS NULL OR f.bot_id IS NULL)
                ORDER BY c.id LIMIT ?""", (str(guild.id), int(limit))).fetchall()
        rows = [dict(r) for r in rows]
    count, errors = 0, []
    for row in rows:
        with get_connection() as db:
            sheet = db.execute(
                "SELECT sheet_url,channel_id,message_id FROM character_sheet_links WHERE guild_id=? AND character_id=?",
                (str(guild.id), row['id']),
            ).fetchone()
            sheet = dict(sheet) if sheet else None
            try:
                template = db.execute(
                    "SELECT raw_text FROM rp_npc_templates_v23 WHERE guild_id=? AND name=? AND state='claimed' ORDER BY id DESC LIMIT 1",
                    (str(guild.id), row['name']),
                ).fetchone()
                body = template['raw_text'] if template else ''
            except Exception:
                body = ''
        if not body and sheet:
            try:
                channel = await _fetch_channel(guild, sheet['channel_id'])
                if channel:
                    message = await channel.fetch_message(int(sheet['message_id']))
                    body = message.content or ''
            except (discord.HTTPException, AttributeError, ValueError):
                pass
        try:
            forum, note = await ensure_official_npc_channel(guild, row['id'], row['name'], body, sheet['sheet_url'] if sheet else '')
            if forum: count += 1
            else: errors.append(f"#{row['id']}：{note}")
        except Exception as exc:
            LOG.exception('官方 NPC #%s 論壇自動補建失敗', row['id'])
            errors.append(f"#{row['id']}：{type(exc).__name__}")
    return count, errors
