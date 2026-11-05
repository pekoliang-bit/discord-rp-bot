
"""自動替既有與新啟用的角色建立網路帳號。

每位角色建立：
- 私人本帳：角色本名
- 官方帳號：藝名（無藝名使用本名）＋ Oficial

不增加新的 Slash 指令。
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from core.network_account_autocreate import (
    backfill_active_characters,
)

LOG = logging.getLogger(__name__)


class NetworkAutoAccounts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.reconcile.start()

    async def cog_unload(self):
        self.reconcile.cancel()

    @tasks.loop(minutes=2)
    async def reconcile(self):
        try:
            count = await asyncio.to_thread(
                backfill_active_characters
            )

            LOG.debug(
                '已同步 %s 名角色的網路帳號',
                count,
            )

        except Exception:
            LOG.exception(
                '角色網路帳號自動補建失敗'
            )

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(
        NetworkAutoAccounts(bot)
    )
