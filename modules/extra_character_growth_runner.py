
"""路人48小時成長排程；與伺服器共用規則和持久化時鐘。"""
from __future__ import annotations
import asyncio
import logging
from discord.ext import commands, tasks
from modules.extra_character_growth import run_guild

LOG = logging.getLogger(__name__)

class ExtraGrowthRunner(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(hours=1)
    async def poll(self):
        for guild in self.bot.guilds:
            try:
                await asyncio.to_thread(
                    run_guild, guild.id
                )
            except Exception:
                LOG.exception(
                    '路人48小時結算失敗 guild=%s',
                    guild.id
                )

    @poll.before_loop
    async def before(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(ExtraGrowthRunner(bot))
