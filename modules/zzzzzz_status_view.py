"""將地位資料加入既有 /查看 角色面板，不註冊新指令。

應在 modules.character_view 與 modules.character_status 之後載入。
"""
from __future__ import annotations

import logging

from discord.ext import commands

LOG = logging.getLogger(__name__)


async def setup(bot: commands.Bot):
    from modules import character_view
    from modules.character_status import (
        LEVELS,
        get_status,
    )

    original = character_view.make_character_embed
    if getattr(original, "_rp_status_integrated", False):
        return

    def with_status(actor, character_id, *, private=False, detail_page=0):
        embed = original(actor, character_id, private=private, detail_page=detail_page)
        try:
            state = get_status(actor.guild_id, character_id)
            level = max(1, min(20, int(state["level"])))
            label = LEVELS[level - 1][1]
            text = f"LV{level}｜{label}"
            if state.get("warning_active"):
                text += "\n⚠️ 目前有地位維持警告"
            embed.add_field(name="🏅 角色地位", value=text, inline=False)
        except Exception:
            LOG.exception("角色 #%s 地位面板載入失敗", character_id)
            embed.add_field(name="🏅 角色地位", value="地位資料暫時無法讀取", inline=False)
        return embed

    with_status._rp_status_integrated = True
    character_view.make_character_embed = with_status
    LOG.info("角色地位已整合至 /查看 面板")
