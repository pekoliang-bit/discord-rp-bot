"""個人店鋪 Discord 頻道自動建立／補建（不註冊斜線指令）。

只處理 personal_shops 已核准、營業中的商店；使用獨立紀錄防止重複。
不能建立頻道時保留商店資料，之後可由週期補建重試。
"""
from __future__ import annotations

import logging
import re

import discord
from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
DEFAULT_GUILD = '1544051203493601380'
DEFAULT_CATEGORY = 1548941879314489424


def initialize():
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS rp_player_shop_channel_settings (
            guild_id TEXT PRIMARY KEY, category_id TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rp_player_shop_channels (
            guild_id TEXT NOT NULL, shop_id INTEGER NOT NULL,
            channel_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,shop_id), UNIQUE(guild_id,channel_id)
        )""")


def category_for(guild_id):
    initialize()
    with get_connection() as db:
        row = db.execute('SELECT category_id FROM rp_player_shop_channel_settings WHERE guild_id=?',
                         (str(guild_id),)).fetchone()
    if row:
        return int(row['category_id'])
    return DEFAULT_CATEGORY if str(guild_id) == DEFAULT_GUILD else None


def unlinked_shops(guild_id):
    initialize()
    with get_connection() as db:
        return [dict(r) for r in db.execute("""
            SELECT s.id, s.name, c.owner_id FROM personal_shops AS s
            JOIN characters AS c ON c.guild_id=s.guild_id AND c.id=s.character_id
            LEFT JOIN rp_player_shop_channels AS link
              ON link.guild_id=s.guild_id AND link.shop_id=s.id
            WHERE s.guild_id=? AND s.status='open' AND link.shop_id IS NULL
            ORDER BY s.id
        """, (str(guild_id),)).fetchall()]


async def provision(guild: discord.Guild, *, shop_id: int | None = None):
    """建立缺失頻道。返回 (建立數量, 失敗訊息)。不更動商店收付款。"""
    category_id = category_for(guild.id)
    if category_id is None:
        return 0, '尚未設定商業分類'
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return 0, '找不到商業經貿分類'
    me = guild.me
    if me is None:
        return 0, '無法確認機器人身分'
    if not category.permissions_for(me).manage_channels:
        return 0, '缺少管理頻道權限'

    created, failures = 0, []
    for item in unlinked_shops(guild.id):
        if shop_id is not None and int(item['id']) != int(shop_id):
            continue
        marker = f'RP_PERSONAL_SHOP:{guild.id}:{item["id"]}'
        # 資料庫寫入失敗後，下次執行會依 topic 找回頻道，不再重建。
        channel = next((c for c in guild.text_channels if c.topic and marker in c.topic), None)
        if channel is None:
            name = re.sub(r'[\r\n\t@#]', '', str(item['name'])).strip()[:75] or '個人商店'
            overwrites = {}
            owner = guild.get_member(int(item['owner_id'])) if str(item['owner_id']).isdigit() else None
            if owner:
                overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            try:
                kwargs = {'name': name, 'category': category,
                          'topic': f'{marker}｜玩家個人商店',
                          'reason': f'已核准個人商店 #{item["id"]} 自動建立'}
                if overwrites:
                    kwargs['overwrites'] = overwrites
                channel = await guild.create_text_channel(**kwargs)
            except (discord.Forbidden, discord.HTTPException) as exc:
                LOG.warning('個人商店 #%s 建立頻道失敗：%s', item['id'], exc)
                failures.append(f'#{item["id"]}: {type(exc).__name__}')
                continue
        try:
            with transaction() as db:
                db.execute('''INSERT OR IGNORE INTO rp_player_shop_channels(guild_id,shop_id,channel_id)
                    VALUES(?,?,?)''', (str(guild.id), int(item['id']), str(channel.id)))
        except Exception:
            LOG.exception('個人商店 #%s 記錄頻道失敗，稍後將依 topic 補登', item['id'])
            failures.append(f'#{item["id"]}: 儲存失敗')
        else:
            created += 1
    return created, '、'.join(failures)
