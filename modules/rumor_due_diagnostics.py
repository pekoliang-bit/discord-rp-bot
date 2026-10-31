
"""傳聞到期唯讀巡檢 Cog；不改變任何案件、金錢或角色數值。

安裝：modules/rumor_due_diagnostics.py
只有 RUMOR_DIAGNOSTICS_ENABLED=1 才啟用；
與正式結算開關獨立。
"""
from __future__ import annotations

import logging
import os

from discord.ext import commands, tasks

from core.rumor_settlement_queue import inspect_due_cases

LOG = logging.getLogger(__name__)


def diagnostics_enabled() -> bool:
    return os.getenv(
        "RUMOR_DIAGNOSTICS_ENABLED", "0"
    ) == "1"


def inspect_once(*, limit: int = 100) -> dict[str, int]:
    """唯讀巡檢所有伺服器的到期案件，依狀態彙總。"""

    if not diagnostics_enabled():
        return {}

    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit 須介於 1～500")

    cases = inspect_due_cases(None, limit=limit)
    counts: dict[str, int] = {}

    for item in cases:
        counts[item.state] = (
            counts.get(item.state, 0) + 1
        )

    if counts:
        LOG.info(
            "傳聞到期唯讀診斷：共 %d 筆；分類=%s",
            len(cases),
            counts,
        )
        if len(cases) == limit:
            LOG.warning(
                "傳聞到期診斷已達上限 %d，"
                "尚可能有更多案件",
                limit,
            )
    else:
        LOG.info(
            "傳聞到期唯讀診斷：沒有待結算案件"
        )

    return counts


class RumorDueDiagnostics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if diagnostics_enabled():
            self.inspect_loop.start()
        else:
            LOG.info(
                "傳聞到期唯讀診斷關閉；"
                "不啟動巡檢排程"
            )

    def cog_unload(self):
        if self.inspect_loop.is_running():
            self.inspect_loop.cancel()

    @tasks.loop(hours=1)
    async def inspect_loop(self):
        if not diagnostics_enabled():
            return

        try:
            import asyncio

            await asyncio.to_thread(inspect_once)

        except Exception:
            LOG.exception(
                "傳聞唯讀巡檢失敗；"
                "不會執行結案或發獎"
            )

    @inspect_loop.before_loop
    async def wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(RumorDueDiagnostics(bot))
