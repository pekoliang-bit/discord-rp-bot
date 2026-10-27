
"""Discord RP 商城首頁暫時相容模組。

此版本暫時停用新版商城首頁的指令註冊，
避免與 modules.shop 的 /商城 指令重複。

保留原本商城的購買與交易功能。
不修改資料庫。
"""

import logging

from discord.ext import commands

LOG = logging.getLogger(__name__)


async def setup(bot: commands.Bot):
    LOG.info(
        "新版商城首頁暫時停用，"
        "保留 modules.shop 原有 /商城 指令，"
        "避免重複註冊。"
    )
