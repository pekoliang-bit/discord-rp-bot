
"""Discord RP：第一批舊角色指令清理。

只停止註冊重複的斜線指令。
不刪除角色、草稿、資料庫或審核紀錄。

正式保留：
/創角
/查看
/審核
/角色切換
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

LOG = logging.getLogger(__name__)

# 第一批：舊角色指令 → 正式替代指令
LEGACY_COMMANDS = {
    "建立角色": "創角",
    "角色填寫": "創角",
    "角色送審": "創角",
    "感情設定": "創角",
    "角色檔案": "查看",
    "角色審核": "審核",
}

# 永遠不能由此模組移除的正式指令
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
    "任務",
    "排行",
    "行動",
    "內容建立",
    "紀律",
    "公司",
    "陣營",
    "合約",
    "頒獎",
    "抽獎",
    "娛樂城",
}


async def setup(bot: commands.Bot):
    """由 main.py 在載入其他模組後執行。"""

    # 使用 discord.AppCommandType
    # 而非 app_commands.AppCommandType
    command_type = (
        discord.AppCommandType.chat_input
    )

    registered = {
        command.name
        for command in bot.tree.get_commands(
            type=command_type
        )
    }

    removed = []
    skipped = []

    for old_name, replacement in LEGACY_COMMANDS.items():

        if old_name in PROTECTED_COMMANDS:
            LOG.warning(
                "受保護指令不可刪除：/%s",
                old_name,
            )
            skipped.append(old_name)
            continue

        if replacement not in registered:
            LOG.warning(
                "正式入口 /%s 不存在，"
                "保留舊指令 /%s",
                replacement,
                old_name,
            )
            skipped.append(old_name)
            continue

        if old_name not in registered:
            LOG.info(
                "舊指令 /%s 未註冊，略過",
                old_name,
            )
            continue

        try:
            removed_command = (
                bot.tree.remove_command(
                    old_name,
                    type=command_type,
                )
            )

            if removed_command is None:
                LOG.warning(
                    "未能移除舊指令：/%s",
                    old_name,
                )
                skipped.append(old_name)
                continue

            removed.append(old_name)

            LOG.info(
                "已移除 /%s，"
                "請改用 /%s",
                old_name,
                replacement,
            )

        except Exception:
            LOG.exception(
                "移除舊指令 /%s 時出錯",
                old_name,
            )
            skipped.append(old_name)

    LOG.info(
        "角色舊指令清理完成："
        "移除 %d 條，略過 %d 條",
        len(removed),
        len(skipped),
    )

    LOG.info(
        "目前頂層斜線指令總數：%d",
        len(
            bot.tree.get_commands(
                type=command_type
            )
        ),
    )

    # 不在這裡呼叫 bot.tree.sync()。
    # 由 main.py 載入全部模組後統一同步。
