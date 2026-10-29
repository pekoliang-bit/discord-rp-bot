"""合併零散斜線指令入口；只移除註冊，不刪除舊功能與資料。"""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

LOG = logging.getLogger(__name__)

PROTECTED = {
    "創角", "查看", "銀行", "商城", "公司", "陣營", "任務",
    "登記", "行動", "排行", "抽獎", "娛樂城", "申請",
    "玩家申請", "審核", "官方", "總覽", "群主指引",
    "頻道商店", "通知", "系統狀態",
}

# 舊入口 → 已規劃的正式面板。正式入口未存在時不清除。
ROUTES = {
    "抽獎紀錄": "抽獎", "抽獎記錄": "抽獎",
    "抽獎商品綁定": "抽獎", "抽獎商品領取": "抽獎",
    "店鋪申請": "申請", "店鋪": "商城",
    "兌換代幣": "商城", "兌換紀錄": "商城",
    "兌換申請": "申請", "兌換審核": "審核",
    "公司管理待審": "公司", "公司管理代審": "公司",
    "公司管理工作審核": "公司", "公司管理工作提交": "公司",
    "審核我的工作": "公司", "應徵審核": "公司",
    "職位": "公司", "職位設定": "公司",
    "管理店鋪待審": "公司", "管理店鋪代審": "公司",
    "管理店鋪等級": "公司", "管理店鋪審核": "審核",
    "商城下架": "商城", "商城新增": "商城", "商城修改": "商城",
    "黑卡通知": "銀行", "活動報名": "任務",
    "活動報名審核": "審核", "活動成果複核": "審核",
    "活動管理員授權": "官方", "活動獎勵結算": "官方",
    "活動獎勵設定": "官方", "活動進程": "任務",
    "活動企劃": "申請", "活動企劃補充": "申請",
    "活動企劃審核": "審核", "活動企劃送審": "申請",
    "我的活動企劃": "申請",
    "任務待審": "審核", "任務代審": "審核",
    "任務建立": "官方", "任務列表": "任務",
    "任務審核": "審核", "任務提交": "任務",
    "任務完成匯報": "任務", "完成匯報": "任務",
    "活動主辦身分組": "官方", "活動專屬頻道": "官方",
    "簡單查看": "查看", "簡單建立": "官方", "簡單獎品": "官方",
    "角色單登記": "創角", "角色論壇設定": "官方",
    "角色啟用": "審核", "角色切換": "查看",
    "劇情回目": "官方", "劇情回目獎勵": "官方",
    "劇情條件設定": "官方", "商品批次匯入說明": "商城",
    "專屬物品使用": "商城", "玩家NPC修改": "查看",
    "信用卡銷戶": "銀行", "銀行利率": "官方",
    "專屬物品裝備": "商城", "專屬物品查看": "商城",
    "專屬物品穿戴": "商城", "專屬物品卸下": "商城",
    "最終數值": "查看", "作品": "查看",
    "內容建立": "官方", "測試": "系統狀態",
}


def destination_of(name: str) -> str | None:
    if name in ROUTES:
        return ROUTES[name]
    if re.match(r"^B(?:0[2-9]|10)", name, re.I):
        return "商城"
    return None


async def setup(bot: commands.Bot):
    """完成每批模組載入後安全撤除舊入口，最後由 main.py 同步。"""
    command_type = discord.AppCommandType.chat_input
    current = {
        cmd.name: cmd
        for cmd in bot.tree.get_commands(type=command_type)
    }
    removed = []
    deferred = []

    for name, command in current.items():
        if name in PROTECTED:
            continue
        destination = destination_of(name)
        if destination is None:
            continue
        if destination == "申請" and destination not in current:
            destination = "玩家申請"
        if destination not in current:
            deferred.append(name)
            continue
        if not isinstance(command, discord.app_commands.Command):
            deferred.append(name)
            continue
        if bot.tree.remove_command(name, type=command_type):
            removed.append(name)

    if hasattr(bot, "hidden_legacy"):
        for name in removed:
            if name not in bot.hidden_legacy:
                bot.hidden_legacy.append(name)

    LOG.info(
        "舊指令整理：移除 %d 條，尚待正式入口 %d 條。",
        len(removed), len(deferred),
    )
    if deferred:
        LOG.info("暫留入口：%s", "、".join(sorted(deferred)))
