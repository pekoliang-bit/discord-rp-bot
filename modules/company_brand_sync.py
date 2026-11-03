"""品牌銷售自動同步服務。

定期將個人店鋪已確認的訂單同步到品牌。
不直接修改角色金錢或商店庫存。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from discord.ext import commands, tasks

from core.company_endorsement_lifecycle import reconcile_expired
from core.company_endorsement_reminders import scan_due
from core.company_endorsement_notifications import sync_private_notices
from modules.company_endorsement_due_notices import forward_due_notices
from core.database import get_connection

from modules.company_brand_progress import (
    initialize as initialize_progress,
)

from modules.company_brand_notices import sync_to_notification_updates

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

    @tasks.loop(minutes=5, reconnect=True)
    async def sales_sync(self):

        try:
            count = await asyncio.to_thread(
                sync_brand_sales
            )

            # 每次同步時整理已到期合約；不執行任何扣款。
            def expire_across_guilds():
                with get_connection() as db:
                    guilds = [r[0] for r in db.execute("SELECT DISTINCT guild_id FROM rp_endorsement_terms WHERE status='active'")] if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_endorsement_terms'").fetchone() else []
                return sum(reconcile_expired(gid) for gid in guilds)
            expired = await asyncio.to_thread(expire_across_guilds)
            if expired: LOG.info('代言合約已到期：%s', expired)
            due_count=await asyncio.to_thread(scan_due)
            if due_count: LOG.info('新增代言期款到期提醒：%s',due_count)
            private_count = await asyncio.to_thread(sync_private_notices)
            if private_count:
                LOG.info('代言事件已排入角色專屬論壇私人通知：%s',private_count)
            sent=await asyncio.to_thread(forward_due_notices)
            if sent: LOG.info('代言期款提醒轉入通知佇列：%s',sent)
            notices = await asyncio.to_thread(sync_to_notification_updates)
            if notices:
                LOG.info("品牌通知已移交至通知彙總佇列：%s 筆。", notices)
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
