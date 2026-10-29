
"""品牌銷售自動同步服務。

定期將個人店鋪已確認的訂單同步到品牌。
不直接修改角色金錢或商店庫存。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from discord.ext import commands, tasks

from modules.company_brand_progress import (
    initialize as initialize_progress,
)

from modules.company_sales_bridge import (
    initialize as initialize_bridge,
    sync_brand_sales,
)


LOG = logging.getLogger(__name__)


class CompanyBrandSync(commands.Cog):

    def __init__(self, bot):

        self.bot = bot
        self.sales_sync.start()

    def cog_unload(self):

        self.sales_sync.cancel()

    @tasks.loop(minutes=5)
    async def sales_sync(self):

        try:
            count = await asyncio.to_thread(
                sync_brand_sales
            )

            if count:
                LOG.info(
                    "品牌營業額同步完成：新增 %s 筆交易。",
                    count,
                )

        except (
            sqlite3.Error,
            ValueError,
            PermissionError,
        ):
            LOG.exception(
                "品牌營業額同步失敗。"
            )

        except Exception:
            LOG.exception(
                "品牌營業額同步發生未預期錯誤。"
            )

    @sales_sync.before_loop
    async def before_sales_sync(self):

        await self.bot.wait_until_ready()


async def setup(bot):

    initialize_progress()
    initialize_bridge()

    await bot.add_cog(
        CompanyBrandSync(bot)
    )
