
"""Discord RP｜官方商店位置與頻道連結管理。

檔案：
modules/shop_location.py

用途：
- 管理 B01～B10 官方商店位置
- 選擇商城內或其他 Discord 頻道
- 驗證頻道、論壇貼文及討論串連結
- 保留現有位置資料
- 群主最高管理權限
- 提供其他商城模組查詢位置

資料：
shop_locations

提供：
init_db()
get_location(guild_id, code)
save_location(...)
validate_discord_link(...)
LocationButton(code)
"""

from __future__ import annotations

import logging
import re

from urllib.parse import urlparse

import discord

from core.database import (
    get_connection,
    transaction,
)


LOG = logging.getLogger(__name__)

TIMEOUT = 300

CODES = {
    f"B{i:02d}"
    for i in range(1, 11)
}

STORE_NAMES = {
    "B01": "服飾精品店",
    "B02": "美妝保養店",
    "B03": "珠寶鐘錶店",
    "B04": "數位科技店",
    "B05": "演藝用品店",
    "B06": "書籍文具店",
    "B07": "家居生活店",
    "B08": "娛樂收藏店",
    "B09": "特殊道具店",
    "B10": "黑卡隱藏商店",
}


# ==========================================
# 權限驗證
# ==========================================

def is_owner(interaction):
    return bool(
        interaction.guild is not None
        and interaction.guild.owner_id
        == interaction.user.id
    )


def require_owner(interaction):
    if not is_owner(interaction):
        raise PermissionError(
            "只有伺服器群主可以管理官方商店位置。"
        )


# ==========================================
# 商店編號驗證
# ==========================================

def normalize_code(code):
    code = str(code or "").strip().upper()

    if code not in CODES:
        raise ValueError(
            "商店編號必須介於 B01～B10。"
        )

    return code


# ==========================================
# 資料庫
# ==========================================

def init_db():
    """建立商店位置資料表，不清除舊資料。"""

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            shop_locations (
                guild_id TEXT NOT NULL,
                store_code TEXT NOT NULL,
                placement TEXT NOT NULL
                    DEFAULT 'mall'
                    CHECK (
                        placement IN (
                            'mall',
                            'channel'
                        )
                    ),
                target_url TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id,
                    store_code
                )
            )
            """
        )


# ==========================================
# 商店位置查詢
# ==========================================

def get_location(guild_id, code):
    """回傳 (placement, target_url)。

    placement:
    - mall：商城內
    - channel：其他 Discord 頻道
    """

    code = normalize_code(code)

    if guild_id is None:
        return "mall", ""

    with get_connection() as conn:
        table = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table'
              AND name='shop_locations'
            """
        ).fetchone()

        if table is None:
            return "mall", ""

        row = conn.execute(
            """
            SELECT placement, target_url
            FROM shop_locations
            WHERE guild_id=?
              AND store_code=?
            """,
            (
                str(guild_id),
                code,
            ),
        ).fetchone()

    if row is None:
        return "mall", ""

    return (
        str(row["placement"]),
        str(row["target_url"] or ""),
    )


# ==========================================
# Discord 連結驗證
# ==========================================

def validate_discord_link(guild_id, url):
    """驗證 Discord 頻道或訊息連結。

    允許：
    https://discord.com/channels/GUILD/CHANNEL

    https://discord.com/channels/GUILD/CHANNEL/MESSAGE

    回傳：
    (完整連結, 頻道 ID)
    """

    url = str(url or "").strip()

    if not url:
        raise ValueError(
            "選擇其他頻道時必須提供 Discord 連結。"
        )

    parsed = urlparse(url)

    allowed_hosts = {
        "discord.com",
        "www.discord.com",
        "discordapp.com",
        "www.discordapp.com",
    }

    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "請提供有效的 Discord HTTPS "
            "頻道或貼文連結。"
        )

    match = re.fullmatch(
        r"/channels/(\d+)/(\d+)(?:/(\d+))?/?",
        parsed.path,
    )

    if match is None:
        raise ValueError(
            "Discord 連結格式錯誤。"
            "請複製頻道或貼文連結。"
        )

    link_guild_id = match.group(1)
    channel_id = int(match.group(2))

    if link_guild_id != str(guild_id):
        raise ValueError(
            "只能設定本伺服器內的 Discord 頻道。"
        )

    normalized = (
        "https://discord.com"
        + parsed.path.rstrip("/")
    )

    return normalized, channel_id


# ==========================================
# 驗證頻道是否存在
# ==========================================

async def resolve_destination(
    interaction,
    url,
):
    """確認連結對應的頻道可由機器人存取。"""

    link, channel_id = validate_discord_link(
        interaction.guild_id,
        url,
    )

    guild = interaction.guild

    if guild is None:
        raise ValueError(
            "請在 Discord 伺服器內操作。"
        )

    channel = guild.get_channel_or_thread(
        channel_id
    )

    if channel is None:
        try:
            channel = await guild.fetch_channel(
                channel_id
            )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ) as exc:
            raise ValueError(
                "機器人無法存取指定頻道。"
                "請確認頻道存在及機器人權限。"
            ) from exc

    if getattr(channel, "guild", None) != guild:
        raise ValueError(
            "指定頻道不屬於目前伺服器。"
        )

    if not isinstance(
        channel,
        (
            discord.TextChannel,
            discord.Thread,
            discord.ForumChannel,
        ),
    ):
        raise ValueError(
            "目前僅支援文字頻道、"
            "論壇頻道或討論串。"
        )

    me = guild.me

    if me is None:
        try:
            me = await guild.fetch_member(
                interaction.client.user.id
            )
        except discord.HTTPException as exc:
            raise ValueError(
                "無法確認機器人的伺服器權限。"
            ) from exc

    permissions = channel.permissions_for(me)

    if not permissions.view_channel:
        raise ValueError(
            "機器人沒有查看該頻道的權限。"
        )

    return link, channel


# ==========================================
# 保存商店位置
# ==========================================

def save_location(
    guild_id,
    code,
    placement,
    url,
    user_id,
):
    code = normalize_code(code)

    placement = str(
        placement or ""
    ).strip().lower()

    if placement not in (
        "mall",
        "channel",
    ):
        raise ValueError(
            "販售位置只能選擇商城或頻道。"
        )

    if placement == "channel":
        url, _ = validate_discord_link(
            guild_id,
            url,
        )
    else:
        url = ""

    init_db()

    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO shop_locations (
                guild_id,
                store_code,
                placement,
                target_url,
                updated_by
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (
                guild_id,
                store_code
            )
            DO UPDATE SET
                placement=excluded.placement,
                target_url=excluded.target_url,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                code,
                placement,
                url,
                str(user_id),
            ),
        )

    name = STORE_NAMES[code]

    if placement == "mall":
        return (
            f"✅ **{code}｜{name}**\n\n"
            "已設定為商城內販售。\n"
            "玩家可以從 `/商城` 進入。"
        )

    return (
        f"✅ **{code}｜{name}**\n\n"
        "已設定為其他頻道販售。\n"
        f"商店連結：{url}\n\n"
        "原有商品及庫存不受影響。"
    )


# ==========================================
# 位置設定表單
# ==========================================

class ShopLocationModal(
    discord.ui.Modal,
    title="設定官方商店販售位置",
):

    placement = discord.ui.TextInput(
        label="販售位置",
        placeholder="輸入：商城 或 頻道",
        required=True,
        max_length=10,
    )

    url = discord.ui.TextInput(
        label="Discord 頻道／論壇貼文連結",
        placeholder="選擇商城時可以留空",
        required=False,
        max_length=300,
    )

    def __init__(self, code):
        super().__init__(
            timeout=TIMEOUT
        )

        self.code = normalize_code(
            code
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        try:
            require_owner(
                interaction
            )

            raw_placement = str(
                self.placement.value
            ).strip().lower()

            placement_map = {
                "商城": "mall",
                "商城內": "mall",
                "mall": "mall",
                "頻道": "channel",
                "其他頻道": "channel",
                "channel": "channel",
            }

            placement = placement_map.get(
                raw_placement
            )

            if placement is None:
                raise ValueError(
                    "請輸入「商城」或「頻道」。"
                )

            url = str(
                self.url.value or ""
            ).strip()

            if placement == "channel":
                url, channel = (
                    await resolve_destination(
                        interaction,
                        url,
                    )
                )

            result = save_location(
                interaction.guild_id,
                self.code,
                placement,
                url,
                interaction.user.id,
            )

            await interaction.response.send_message(
                result[:1900],
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
            discord.HTTPException,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 管理入口按鈕
# ==========================================

class LocationButton(discord.ui.Button):
    """供 official_shop.py 使用。"""

    def __init__(self, code):
        code = str(
            code or ""
        ).strip().upper()

        valid = code in CODES

        super().__init__(
            label="📍 販售位置／連結",
            style=discord.ButtonStyle.secondary,
            row=3,
            disabled=not valid,
        )

        self.code = code

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        try:
            require_owner(
                interaction
            )

            if self.code not in CODES:
                raise ValueError(
                    "請先選擇 B01～B10 商店。"
                )

            await interaction.response.send_modal(
                ShopLocationModal(
                    self.code
                )
            )

        except (
            PermissionError,
            ValueError,
        ) as exc:
            await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )


# ==========================================
# 模組初始化
# ==========================================

async def setup(bot):
    init_db()

    LOG.info(
        "商店位置管理系統已載入："
        "B01～B10、商城內、其他頻道與連結。"
    )
