"""新手指令成功偵測：只計算本人論壇的「機器人使用」帖子。

本檔是 Discord extension；由 main.py 的 setup() 自動載入。
重要：這只負責成功事件紀錄，不能替代各指令原本的頻道檢查。
"""
from __future__ import annotations

import logging
import discord
from discord.ext import commands

from core.database import get_connection
from modules.task_repository import initialize
from modules.task_newbie import (
    REQUIRED_COMMANDS, record_success, progress, can_use_training_channel,
)

LOG = logging.getLogger(__name__)


def training_allowed(guild_id, character_id, user_id, channel_id, command):
    return can_use_training_channel(guild_id, character_id, user_id, channel_id, command)


def completed_tutorial_view(guild_id, character_id, user_id, channel_id, command):
    if command not in REQUIRED_COMMANDS:
        return False
    initialize()
    return record_success(guild_id, character_id, user_id, channel_id, command)


def tutorial_channel_decision(guild_id, character_id, user_id, channel_id, command):
    from modules.task_newbie import newbie_required_channel
    initialize()
    return newbie_required_channel(guild_id, character_id, user_id, channel_id, command)

def tutorial_status(guild_id, character_id):
    initialize()
    return progress(guild_id, character_id)


def character_for_training_thread(guild_id, user_id, thread_id):
    """以 Discord ID 關聯論壇帖子；不使用可任意更名的暱稱。"""
    if not guild_id or not thread_id:
        return None
    with get_connection() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='character_forum_links'"
        ).fetchone()
        if not exists:
            return None
        rows = db.execute(
            """SELECT c.id FROM characters AS c
               JOIN character_forum_links AS f
                 ON f.guild_id=c.guild_id AND f.character_id=c.id
               WHERE c.guild_id=? AND c.owner_id=?
                 AND c.character_type='player'
                 AND c.status IN ('active','approved')
                 AND f.bot_id=?""",
            (str(guild_id), str(user_id), str(thread_id)),
        ).fetchall()
    # 不接受含糊的論壇綁定，避免記到錯誤角色。
    return int(rows[0]['id']) if len(rows) == 1 else None


class TaskEventBridge(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener('on_app_command_completion')
    async def record_newbie_on_success(self, interaction: discord.Interaction, command):
        """僅當 Discord app command callback 正常完成才執行。

        注意：callback 正常 return 仍可能是業務失敗（例如拒絕訊息）。
        因此本監聽器只作保守的「首頁導覽」紀錄：其餘完整成功
        判定應由各指令實際成功顯示介面後呼叫 completed_tutorial_view。
        """
        try:
            name = getattr(command, 'name', None)
            if name not in REQUIRED_COMMANDS:
                return
            if not interaction.guild_id or not interaction.channel_id:
                return
            character_id = character_for_training_thread(
                interaction.guild_id, interaction.user.id, interaction.channel_id
            )
            if character_id is None:
                return
            # 不可把拒絕存取或失敗回覆直接視為「成功使用」。
            # 真正的成功事件由各指令自行呼叫 completed_tutorial_view。
            LOG.debug('新手指令待成功確認 guild=%s character=%s command=%s',
                      interaction.guild_id, character_id, name)
        except Exception:
            LOG.exception('新手指令事件觀察發生異常，不影響原指令')


async def setup(bot: commands.Bot):
    initialize()
    await bot.add_cog(TaskEventBridge(bot))
