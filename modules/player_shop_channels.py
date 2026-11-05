"""已核准個人商店的 Discord 頻道建立、補建及分類同步。

使用專案既有 rp_player_shop_channel_settings / rp_player_shop_channels 表。
由 modules.player_shops 於核准後及定時巡檢時呼叫 provision()。
不新增斜線指令，也不另外啟動背景 Cog。
"""
from __future__ import annotations

import asyncio
import logging
import re

import discord

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)
DEFAULT_GUILD = "1544051203493601380"
DEFAULT_CATEGORY = 1548941879314489424

# 避免同一個 Python 程序內的審核回呼與週期補建同時建立重複頻道。
_GUILD_LOCKS: dict[int, asyncio.Lock] = {}


def initialize():
    with transaction() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_player_shop_channel_settings (
                guild_id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS rp_player_shop_channels (
                guild_id TEXT NOT NULL,
                shop_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, shop_id),
                UNIQUE (guild_id, channel_id)
            )
        """)


def category_for(guild_id):
    initialize()
    with get_connection() as db:
        row = db.execute(
            "SELECT category_id FROM rp_player_shop_channel_settings WHERE guild_id=?",
            (str(guild_id),),
        ).fetchone()
    if row:
        value = str(row["category_id"])
        return int(value) if value.isdecimal() else None
    return DEFAULT_CATEGORY if str(guild_id) == DEFAULT_GUILD else None


def set_category(guild_id, category_id):
    """由已有群主權限驗證的設定入口呼叫。"""
    category_id = int(category_id)
    if category_id <= 0:
        raise ValueError("分類 ID 不正確")
    initialize()
    with transaction() as db:
        db.execute("""
            INSERT INTO rp_player_shop_channel_settings(guild_id, category_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET category_id=excluded.category_id
        """, (str(guild_id), str(category_id)))


def _shops(guild_id, shop_id=None):
    """只有真正核准、仍營業的個人商店可以分配頻道。"""
    initialize()
    query = """
        SELECT s.id, s.name, c.owner_id, link.channel_id
          FROM personal_shops AS s
          JOIN personal_shop_applications AS a
            ON a.guild_id=s.guild_id AND a.id=s.application_id
          JOIN characters AS c
            ON c.guild_id=s.guild_id AND c.id=s.character_id
          LEFT JOIN rp_player_shop_channels AS link
            ON link.guild_id=s.guild_id AND link.shop_id=s.id
         WHERE s.guild_id=? AND s.status='open' AND a.status='approved'
    """
    params = [str(guild_id)]
    if shop_id is not None:
        query += " AND s.id=?"
        params.append(int(shop_id))
    query += " ORDER BY s.id"
    with get_connection() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


def unlinked_shops(guild_id):
    """保留舊模組呼叫介面；只回傳尚無紀錄的商店。"""
    return [row for row in _shops(guild_id) if not row["channel_id"]]


def _marker(guild_id, shop_id):
    return f"RP_PERSONAL_SHOP:{guild_id}:{shop_id}"


def _match_topic(channel, marker):
    """確認完整標記，避免 #12 意外匹配 #123。"""
    topic = channel.topic or ""
    return bool(re.search(re.escape(marker) + r"(?!\d)", topic))


def _save_channel(guild_id, shop_id, channel_id):
    with transaction() as db:
        db.execute("""
            INSERT INTO rp_player_shop_channels(guild_id, shop_id, channel_id)
            VALUES(?, ?, ?)
            ON CONFLICT(guild_id, shop_id)
            DO UPDATE SET channel_id=excluded.channel_id
        """, (str(guild_id), int(shop_id), str(channel_id)))


def _forget_missing(guild_id, shop_id, old_channel_id):
    with transaction() as db:
        db.execute("""
            DELETE FROM rp_player_shop_channels
            WHERE guild_id=? AND shop_id=? AND channel_id=?
        """, (str(guild_id), int(shop_id), str(old_channel_id)))


async def provision(guild: discord.Guild, *, shop_id: int | None = None):
    """核准後立即呼叫，並由原本的每小時監控補建。

    返回：(新建或補登數量, 錯誤摘要)。
    若已有綁定則檢查分類並修正，不重建頻道。
    """
    category_id = category_for(guild.id)
    if category_id is None:
        return 0, "尚未設定商業分類"
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return 0, "找不到商業經貿分類"
    me = guild.me
    if me is None or not category.permissions_for(me).manage_channels:
        return 0, "缺少管理頻道權限"

    lock = _GUILD_LOCKS.setdefault(guild.id, asyncio.Lock())
    async with lock:
        count, failures = 0, []
        try:
            shops = _shops(guild.id, shop_id=shop_id)
        except Exception:
            LOG.exception("讀取已核准商店失敗：%s", guild.id)
            return 0, "讀取個人商店資料失敗"

        for item in shops:
            sid = int(item["id"])
            marker = _marker(guild.id, sid)
            old_id = item["channel_id"]
            channel = None
            try:
                if old_id:
                    channel = guild.get_channel(int(old_id))
                    if channel is not None and not isinstance(channel, discord.TextChannel):
                        raise RuntimeError("已綁定的頻道不是文字頻道")
                    if channel is not None and not _match_topic(channel, marker):
                        raise RuntimeError("已綁定頻道缺少正確商店識別碼，拒絕移動")

                if channel is None:
                    # 寫入資料庫失敗後可從 topic 找回剛建立的頻道。
                    channel = next(
                        (c for c in guild.text_channels if _match_topic(c, marker)),
                        None,
                    )

                if channel is None:
                    # 舊的資料庫頻道已被刪除，允許補建。
                    if old_id:
                        _forget_missing(guild.id, sid, old_id)
                    name = re.sub(r"[\r\n\t@#]", "", str(item["name"])).strip()[:75] or "個人商店"
                    overwrites = {}
                    owner_value = str(item["owner_id"])
                    owner = guild.get_member(int(owner_value)) if owner_value.isdecimal() else None
                    if owner is not None:
                        overwrites[owner] = discord.PermissionOverwrite(
                            view_channel=True, send_messages=True
                        )
                    kwargs = {
                        "name": name,
                        "category": category,
                        "topic": f"{marker}｜玩家個人商店",
                        "reason": f"已核准個人商店 #{sid} 自動建立",
                    }
                    if overwrites:
                        kwargs["overwrites"] = overwrites
                    channel = await guild.create_text_channel(**kwargs)
                    LOG.info("新建商店頻道 guild=%s shop=%s channel=%s", guild.id, sid, channel.id)

                elif channel.category_id != category.id:
                    # 不同步覆蓋原有頻道權限。
                    await channel.edit(
                        category=category,
                        sync_permissions=False,
                        reason=f"已核准個人商店 #{sid} 自動歸類",
                    )

                if not old_id or str(old_id) != str(channel.id):
                    _save_channel(guild.id, sid, channel.id)
                    count += 1

            except (discord.HTTPException, RuntimeError, ValueError, OSError) as exc:
                LOG.warning("同步商店頻道失敗 guild=%s shop=%s：%s", guild.id, sid, exc)
                failures.append(f"#{sid}: {exc}")
            except Exception:
                LOG.exception("同步商店頻道發生異常 guild=%s shop=%s", guild.id, sid)
                failures.append(f"#{sid}: 未預期錯誤")

        return count, "、".join(failures)
