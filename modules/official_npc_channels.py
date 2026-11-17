"""官方角色專屬文字頻道：每角色一頻道，統一放在指定分類。

僅對指定伺服器自動建置；不修改原創角色／玩家 NPC 的論壇流程。
"""
from __future__ import annotations

import logging
import re

import discord
from core.database import get_connection, transaction

LOGGER = logging.getLogger(__name__)
OFFICIAL_GUILD_ID = 1544051203493601380
OFFICIAL_CATEGORY_ID = 1545373529019777025
FACTIONS = ('單打獨鬥', '經紀公司', 'MCN', '另闢蹊徑', '熟人抱團', '抱團取暖')
FIELD = re.compile(r'^\s*(?:[-*]\s*)?(?:歸屬|所屬陣營|陣營|角色陣營|所屬勢力)\s*[：:]\s*(.+?)\s*$', re.M)


def extract_faction(sheet_text: str) -> str:
    """以角色單明示陣營為準，未填寫時保留『未分配』而不猜測。"""
    match = FIELD.search(sheet_text or '')
    if not match:
        return '未分配'
    raw = match.group(1).strip()
    for faction in FACTIONS:
        if faction.casefold() in raw.casefold():
            return faction
    return raw[:40] if raw else '未分配'


def channel_title(name: str, faction: str) -> str:
    # Discord 文字頻道可能將空格正規化為連字號；仍以要求的名稱建立。
    return f'{name.strip()} {faction.strip()}'[:100]


def _init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_official_npc_channels (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, character_id)
    )''')


async def ensure_official_npc_channel(guild: discord.Guild, character_id: int,
                                      name: str, sheet_text: str, source_url: str = '') -> tuple[discord.TextChannel | None, str]:
    """建立/回傳角色專屬頻道；失敗時回報訊息，不影響已成功的角色登記。"""
    if guild.id != OFFICIAL_GUILD_ID:
        return None, '此伺服器尚未設定官方角色專屬分類'
    faction = extract_faction(sheet_text)
    with get_connection() as db:
        _init(db)
        existing = db.execute('SELECT channel_id FROM rp_official_npc_channels WHERE guild_id=? AND character_id=?',
                              (str(guild.id), int(character_id))).fetchone()
    if existing:
        old = guild.get_channel(int(existing['channel_id']))
        if old is None:
            try:
                old = await guild.fetch_channel(int(existing['channel_id']))
            except (discord.NotFound, discord.Forbidden):
                old = None
        if isinstance(old, discord.TextChannel):
            return old, '既有官方角色頻道'

    category = guild.get_channel(OFFICIAL_CATEGORY_ID)
    if category is None:
        try:
            category = await guild.fetch_channel(OFFICIAL_CATEGORY_ID)
        except (discord.NotFound, discord.Forbidden):
            return None, '找不到指定分類，或機器人無權讀取'
    if not isinstance(category, discord.CategoryChannel):
        return None, '指定 ID 不是 Discord 頻道分類'
    me = guild.me
    if me is not None and not category.permissions_for(me).manage_channels:
        return None, '機器人在指定分類沒有「管理頻道」權限'

    title = channel_title(name, faction)
    try:
        created = await guild.create_text_channel(
            name=title, category=category,
            topic=f'官方角色｜{name}｜{faction}｜角色 ID：{character_id}',
            reason=f'正式登記官方角色 {character_id}',
        )
        body = f'🎭 **官方角色：{discord.utils.escape_mentions(name)}**\n陣營：{discord.utils.escape_mentions(faction)}\n'
        if source_url:
            body += f'原始角色單：{source_url}\n'
        if sheet_text:
            body += '\n' + discord.utils.escape_mentions(sheet_text)[:1500]
        try:
            await created.send(body[:1900], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            LOGGER.exception('官方角色頻道已建立，但角色單內容發送失敗：%s', character_id)
        with transaction() as db:
            _init(db)
            db.execute('INSERT INTO rp_official_npc_channels(guild_id,character_id,channel_id) VALUES(?,?,?) '
                       'ON CONFLICT(guild_id,character_id) DO UPDATE SET channel_id=excluded.channel_id',
                       (str(guild.id), int(character_id), str(created.id)))
        return created, '已建立官方角色專屬頻道'
    except discord.HTTPException:
        LOGGER.exception('建立官方角色頻道失敗：%s', character_id)
        return None, '建立頻道失敗，請檢查權限與分類頻道數量'
