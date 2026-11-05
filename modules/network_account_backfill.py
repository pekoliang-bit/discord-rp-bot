"""定時補齊已啟用角色的私人及官方網路帳號；不新增斜線指令。"""
from __future__ import annotations

import asyncio
import logging
from discord.ext import commands, tasks
from core.network_groups import ensure_default_accounts, missing_default_characters

LOG = logging.getLogger(__name__)


class NetworkAccountBackfill(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.sync_accounts.start()

    def cog_unload(self):
        self.sync_accounts.cancel()

    @tasks.loop(minutes=10)
    async def sync_accounts(self):
        for guild in self.bot.guilds:
            try:
                pending = await asyncio.to_thread(missing_default_characters, guild.id, 100)
            except Exception:
                LOG.exception('查詢待補建網路帳號失敗 guild=%s', guild.id)
                continue
            for cid in pending:
                try:
                    await asyncio.to_thread(ensure_default_accounts, guild.id, cid)
                except Exception:
                    LOG.exception('補建網路帳號失敗 guild=%s character=%s', guild.id, cid)

    @sync_accounts.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(NetworkAccountBackfill(bot))
