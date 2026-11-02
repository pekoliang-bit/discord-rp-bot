
"""路人角色48小時成長排程。

每小時檢查一次是否有到期結算。
不額外註冊 Slash 指令。
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import commands, tasks

from modules.extra_character_growth import run_guild


LOG = logging.getLogger(__name__)


class ExtraGrowthRunner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self.poll.start()

    def cog_unload(self):
        if self.poll.is_running():
            self.poll.cancel()

    @tasks.loop(hours=1)
    async def poll(self):
        if self._lock.locked():
            return

        async with self._lock:
            for guild in list(self.bot.guilds):
                try:
                    result = await asyncio.to_thread(
                        run_guild,
                        guild.id,
                    )

                    cycles = int(
                        result.get("cycles_applied", 0)
                    )

                    if cycles:
                        LOG.info(
                            "路人結算完成 "
                            "guild=%s characters=%s cycles=%s",
                            guild.id,
                            result.get("characters", 0),
                            cycles,
                        )

                except asyncio.CancelledError:
                    raise

                except Exception:
                    LOG.exception(
                        "路人48小時結算失敗 guild=%s",
                        guild.id,
                    )

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    if bot.get_cog("ExtraGrowthRunner") is None:
        await bot.add_cog(
            ExtraGrowthRunner(bot)
        )
