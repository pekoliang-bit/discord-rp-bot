"""V11 背景結算入口：不註冊任何 Slash 指令。僅處理明確啟用的伺服器。"""
from __future__ import annotations
import asyncio
import logging
from discord.ext import commands, tasks
from core.database import get_connection
from modules.leaderboard_storage import initialize
from modules.leaderboard_scheduler import run_due
from modules.leaderboard_delivery import deliver_period

LOG = logging.getLogger(__name__)

class LeaderboardRuntime(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=5)
    async def tick(self):
        try:
            initialize()
            with get_connection() as db:
                guilds = [r['guild_id'] for r in db.execute('SELECT guild_id FROM lb_v11_config WHERE enabled=1').fetchall()]
            for guild_id in guilds:
                try:
                    results = await asyncio.to_thread(run_due, guild_id)
                    for result in results:
                        if result.get('status') in ('snapshotted', 'already'):
                            await asyncio.to_thread(deliver_period, guild_id, result['cycle'], result['index'])
                        elif result.get('status') == 'requires_historical_recovery':
                            LOG.warning('排行榜需人工補歷史快照 guild=%s cycle=%s index=%s', guild_id, result['cycle'], result['index'])
                except Exception:
                    LOG.exception('排行榜結算異常 guild=%s', guild_id)
        except Exception:
            LOG.exception('排行榜背景工作失敗')

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(LeaderboardRuntime(bot))
