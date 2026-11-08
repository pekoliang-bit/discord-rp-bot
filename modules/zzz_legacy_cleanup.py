"""合併零散斜線指令入口；只移除註冊，不刪除舊功能與資料。"""

from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands

LOG = logging.getLogger(__name__)

PROTECTED = {
    "創角", "查看", "登記", "公司", "官方", "劇情", "群主指引", "商城",
    "申請", "審核", "娛樂城", "惡意", "任務", "總覽", "排行", "系統狀態",
    "銀行", "陣營", "抽獎", "傳聞", "網路", "行動", "通知", "資產",
}

# 舊入口 → 已規劃的正式面板。正式入口未存在時不清除。
ROUTES = {
    # 舊版群組本身也要移除；否則會連同所有子指令留在 Discord。
    "公司管理": "公司", "兌換": "商城", "管理": "商城",
    "活動任務待審": "審核", "活動任務審核": "審核",
    "活動任務建立": "官方", 
     "活動群主確認": "官方",
     "活動成果覆核": "審核",
    "活動管理": "官方", "官方檢查": "官方",
    # 第二批截圖：已經有對應主面板的舊入口。
     
    "玩家npc修改": "查看", "玩家NPC修改": "查看",
    "商品批次匯入": "商城", "商品批次說明": "商城",
    "使用專屬物品": "商城", "專屬物品": "商城",
    "追蹤數值": "查看", "裝備": "商城",
    "角色論壇補建": "官方",
    "獎單建立": "抽獎", "獎單查看": "抽獎",
    "獎單獎品": "抽獎",
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
    "黑卡通知": "銀行", 
    "活動報名審核": "審核", "活動成果複核": "審核",
    "活動管理員授權": "官方", "活動獎勵結算": "官方",
    "活動獎勵設定": "官方", 
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
    "最終數值": "查看", "作品": "任務",
    "內容建立": "官方", "測試": "系統狀態",
    "稱號": "查看", "新手指引": "總覽", "新人禮包管理": "抽獎",
    "角色": "查看",
}


def destination_of(name: str) -> str | None:
    if name in ROUTES:
        return ROUTES[name]
    if re.match(r"^B(?:0[2-9]|10)", name, re.I):
        return "商城"
    return None


async def setup(bot: commands.Bot):
    """提供舊指令對照表；實際清理由 main.py 在正確時機執行。

    此模組可能早於 modules.shop 與 modules.player_shops 載入，
    因此不能在 setup() 移除 /管理 等被其他模組依賴的群組。
    """
    LOG.info("舊指令對照表已載入；等待所有相依模組就緒後清理群組。")
    return


def cleanup_after_extensions(bot: commands.Bot):
    """供獨立部署／手動診斷使用；正常啟動由 main.py 統一清理。"""
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
        if not isinstance(command, (discord.app_commands.Command, discord.app_commands.Group)):
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
