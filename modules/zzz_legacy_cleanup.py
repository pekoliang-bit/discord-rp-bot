
"""Discord RP：舊斜線指令清理。

只移除舊指令入口，不刪除：
- Python 模組
- SQLite 資料
- 角色與草稿
- 審核紀錄

第一批：角色系統六條重複指令。
"""

from __future__ import annotations

import logging

from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger(__name__)

# 舊指令：對應的正式新入口
LEGACY_COMMANDS = {
    "建立角色": "創角",
    "角色填寫": "創角",
    "角色送審": "創角",
    "感情設定": "創角",
    "角色檔案": "查看",
    "角色審核": "審核",
}

# 不准移除的正式入口
PROTECTED_COMMANDS = {
    "創角",
    "查看",
    "角色切換",
    "銀行",
    "商城",
    "玩家申請",
    "審核",
    "官方",
    "登記",
    "總覽",
}


async def setup(bot: commands.Bot):
    registered = {
        command.name
        for command in bot.tree.get_commands(
            type=app_commands.AppCommandType.chat_input
        )
    }

    removed = []
    skipped = []

    for old_name, replacement in LEGACY_COMMANDS.items():
        # 防止誤刪正式指令
        if old_name in PROTECTED_COMMANDS:
            LOG.warning(
                "受保護指令不可移除：/%s",
                old_name,
            )
            skipped.append(old_name)
            continue

        # 新入口必須已經存在
        if replacement not in registered:
            LOG.warning(
                "找不到替代指令 /%s，"
                "保留舊指令 /%s",
                replacement,
                old_name,
            )
            skipped.append(old_name)
            continue

        # 舊入口不存在，直接略過
        if old_name not in registered:
            LOG.info(
                "舊指令 /%s 已不存在",
                old_name,
            )
            continue

        command = bot.tree.remove_command(
            old_name,
            type=app_commands.AppCommandType.chat_input,
        )

        if command is None:
            LOG.warning(
                "無法移除 /%s",
                old_name,
            )
            skipped.append(old_name)
            continue

        removed.append(old_name)

        LOG.info(
            "已移除舊指令 /%s，"
            "正式入口為 /%s",
            old_name,
            replacement,
        )

    LOG.info(
        "角色舊指令清理完成："
        "移除 %s 條，略過 %s 條",
        len(removed),
        len(skipped),
    )

    # 不在此處自行 sync。
    # 交由 main.py 載入全部模組後統一同步。
