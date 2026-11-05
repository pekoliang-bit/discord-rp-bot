
"""商店頻道自動歸類。

在商店申請核准後，由現有申請／審核系統呼叫
ensure_approved_shop_channel()。

不註冊新的斜線指令。
不自行批准商店申請。
不直接修改商店經營資料庫。
"""

from __future__ import annotations

import logging
import re

import discord

from modules.business_location_defaults import (
    get_category_id,
)


LOG = logging.getLogger(__name__)


class ShopChannelRoutingError(RuntimeError):
    pass


def normalize_channel_name(name: str) -> str:
    """清理新商店頻道名稱。"""
    if not isinstance(name, str):
        raise ValueError("商店名稱必須是文字")

    name = name.strip()
    name = re.sub(r"\s+", "-", name)

    if not name:
        raise ValueError("商店名稱不得為空白")

    return name[:100]


def resolve_business_category(
    guild: discord.Guild,
    *,
    custom_category_id: int | str | None = None,
) -> discord.CategoryChannel:
    """尋找本伺服器設定的商業經貿分類。"""
    category_id = get_category_id(
        guild.id,
        custom_category_id=custom_category_id,
    )

    if category_id is None:
        raise ShopChannelRoutingError(
            "此伺服器尚未綁定商業經貿分類。"
        )

    category = guild.get_channel(category_id)

    if not isinstance(
        category,
        discord.CategoryChannel,
    ):
        raise ShopChannelRoutingError(
            "找不到指定的商業經貿分類頻道。"
        )

    return category


def require_manage_channels(guild: discord.Guild):
    member = guild.me

    if member is None:
        raise ShopChannelRoutingError(
            "無法確認機器人在伺服器中的權限。"
        )

    if not member.guild_permissions.manage_channels:
        raise ShopChannelRoutingError(
            "機器人缺少「管理頻道」權限。"
        )


async def ensure_approved_shop_channel(
    guild: discord.Guild,
    *,
    application_id: int | str,
    shop_name: str,
    owner_id: int,
    approved: bool,
    existing_channel_id: int | None = None,
    custom_category_id: int | str | None = None,
) -> discord.TextChannel:
    """核准後建立或移動商店頻道。

    application_id：正式申請案件 ID
    shop_name：核准的商店名稱
    owner_id：店主持有者 Discord ID
    approved：必須由可信任的審核系統提供
    existing_channel_id：既有商店頻道 ID（如有）
    custom_category_id：其他伺服器群主設定的分類 ID

    回傳頻道物件後，呼叫端必須持久化：
    application_id、owner_id、channel.id、guild.id。
    """

    if approved is not True:
        raise ShopChannelRoutingError(
            "商店申請尚未核准，不可建立頻道。"
        )

    if not application_id or not str(application_id).strip():
        raise ShopChannelRoutingError(
            "缺少商店申請案件 ID。"
        )

    if not isinstance(guild, discord.Guild):
        raise ShopChannelRoutingError(
            "必須提供有效的 Discord 伺服器。"
        )

    if type(owner_id) is not int or owner_id <= 0:
        raise ShopChannelRoutingError(
            "商店持有人 ID 無效。"
        )

    require_manage_channels(guild)

    category = resolve_business_category(
        guild,
        custom_category_id=custom_category_id,
    )

    # 已有商店頻道：移入商業經貿分類。
    if existing_channel_id is not None:
        channel = guild.get_channel(
            int(existing_channel_id)
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            raise ShopChannelRoutingError(
                "指定的商店頻道不存在或不是文字頻道。"
            )

        if channel.guild.id != guild.id:
            raise ShopChannelRoutingError(
                "不得移動其他伺服器的頻道。"
            )

        if channel.category_id != category.id:
            await channel.edit(
                category=category,
                sync_permissions=False,
                reason=(
                    f"商店申請 {application_id} 核准，"
                    "移入商業經貿區"
                ),
            )

        return channel

    # 新商店頻道：建立在指定分類。
    channel_name = normalize_channel_name(
        shop_name
    )

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        topic=(
            f"商店名稱：{shop_name}\n"
            f"持有人 ID：{owner_id}\n"
            f"申請案件 ID：{application_id}"
        ),
        reason=(
            f"商店申請 {application_id} 已核准"
        ),
    )

    LOG.info(
        "已建立商店頻道：guild=%s application=%s "
        "owner=%s channel=%s",
        guild.id,
        application_id,
        owner_id,
        channel.id,
    )

    return channel
