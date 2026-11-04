"""Discord 任務獎勵週期補發。不增加斜線指令。

系統會在登入就緒後每十分鐘嘗試補發一次；任務結算唯一鍵由
modules.task_rewards 提供，失敗的項目保留在 completed。
"""
from __future__ import annotations
import asyncio
import logging
import discord
from discord.ext import commands, tasks
from core.database import get_connection
from modules.task_reward_worker import retry_pending

LOG = logging.getLogger(__name__)


class TaskRewardScheduler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.run_pending.start()

    def cog_unload(self):
        self.run_pending.cancel()

    @tasks.loop(minutes=10)
    async def run_pending(self):
        # 只查正式有未領取任務的伺服器，不掃描全部角色。
        try:
            with get_connection() as db:
                rows = db.execute("SELECT DISTINCT guild_id FROM rp_task_progress "
                                  "WHERE status='completed'").fetchall()
        except Exception:
            LOG.exception('任務補發掃描失敗')
            return
        for row in rows:
            gid = str(row['guild_id'])
            if not self.bot.get_guild(int(gid)):
                continue
            try:
                result = await asyncio.to_thread(retry_pending, gid, limit=100)
                if result['errors']:
                    LOG.warning('任務補發仍有未完成項目 guild=%s errors=%d', gid, len(result['errors']))
            except Exception:
                LOG.exception('任務補發排程異常 guild=%s', gid)

    @run_pending.before_loop
    async def before_run_pending(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TaskRewardScheduler(bot))
