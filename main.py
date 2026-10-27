
"""Discord RP 主程式｜正式指令管理版。

功能：
- 初始化 SQLite 資料庫
- 自動載入 modules 中全部功能模組
- 維持商城模組的特殊載入順序
- 自動停用六條重複的角色舊指令
- 保留其他尚未整合的舊指令
- 同步 Discord 全域指令
- 支援 TEST_GUILD_ID 測試伺服器同步
- 管理員系統狀態
- 完整錯誤紀錄

Railway 環境變數：
DISCORD_TOKEN
DATABASE_PATH=/app/data/bot.sqlite3
TEST_GUILD_ID（選填）
RP_HIDE_LEGACY_COMMANDS=0（預設）

注意：
指令清理只影響斜線指令註冊，
不刪除角色、草稿、銀行紀錄或資料庫。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from core.config import (
    get_token,
    get_test_guild_id,
)
from core.database import init_db


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s: %(message)s"
    ),
)

LOG = logging.getLogger("discord_rp_bot")

BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR / "modules"


# 正式規劃的 21 條頂層指令
OFFICIAL_COMMANDS = {
    "創角": "建立角色與草稿",
    "查看": "角色與 NPC 資訊",
    "角色切換": "角色管理",
    "銀行": "銀行及金融業務",
    "商城": "商城及背包",
    "公司": "公司經營",
    "陣營": "陣營管理",
    "合約": "合約管理",
    "任務": "任務中心",
    "登記": "作品及活動登記",
    "行動": "RP 行動",
    "排行": "排行榜",
    "頒獎": "獎項及榮譽",
    "抽獎": "抽獎活動",
    "娛樂城": "娛樂活動",
    "玩家申請": "玩家申請",
    "審核": "審核中心",
    "紀律": "舉報及申訴",
    "官方": "官方管理",
    "內容建立": "官方內容建立",
    "總覽": "系統總覽",
}


# 目前仍可能存在的舊指令
LEGACY_COMMANDS = {
    "建立角色",
    "角色",
    "角色填寫",
    "感情設定",
    "角色檔案",
    "角色送審",
    "角色審核",
    "初始配點",
    "屬性清單",
    "設定官方屬性",
    "屬性",
    "店鋪申請",
    "貸款申請",
    "貸款審核",
    "貸款待審",
    "信用卡申請",
    "信用卡待審",
    "信用卡審核",
    "黑卡申請",
    "黑卡待審",
    "黑卡審核",
    "銀行利率",
    "存款",
    "角色轉帳",
}


# 第一批確定停用的角色舊指令
# 必須先確認替代指令存在
CHARACTER_LEGACY_MAP = {
    "建立角色": "創角",
    "角色填寫": "創角",
    "角色送審": "創角",
    "感情設定": "創角",
    "角色檔案": "查看",
    "角色審核": "審核",
}


# 第二批舊指令：
# 只有管理員明確啟用環境變數後才隱藏。
# 未確認替代功能之前請保持關閉。
OPTIONAL_HIDE_COMMANDS = {
    "初始配點",
    "店鋪申請",
    "貸款申請",
    "信用卡申請",
    "黑卡申請",
    "存款",
    "角色轉帳",
}


# 原專案的商城依賴載入順序
PRIORITY_MODULES = (
    "modules.shop",
    "modules.player_shops",
    "modules.storefront",
    "modules.shop_integration",
)


def hide_legacy_enabled() -> bool:
    raw = os.getenv(
        "RP_HIDE_LEGACY_COMMANDS",
        "0",
    ).strip().lower()

    return raw in {
        "1",
        "true",
        "yes",
        "on",
    }


def discover_extensions() -> list[str]:
    """自動搜尋 modules 資料夾。"""

    if not MODULES_DIR.is_dir():
        raise RuntimeError(
            "找不到 modules 資料夾。"
        )

    extensions = []

    for path in sorted(
        MODULES_DIR.glob("*.py")
    ):
        if path.name.startswith("_"):
            continue

        extensions.append(
            f"modules.{path.stem}"
        )

    normal = [
        name
        for name in extensions
        if name not in PRIORITY_MODULES
    ]

    priority = [
        name
        for name in PRIORITY_MODULES
        if name in extensions
    ]

    ordered = normal + priority

    if len(ordered) != len(set(ordered)):
        raise RuntimeError(
            "模組載入清單存在重複項目。"
        )

    return ordered


def collect_top_level_commands(tree):
    return list(
        tree.get_commands()
    )


def audit_commands(tree):
    """盤點目前本地註冊的頂層指令。"""

    names = [
        command.name
        for command in collect_top_level_commands(
            tree
        )
    ]

    counts = Counter(names)
    actual = set(names)

    return {
        "total": len(names),
        "duplicates": sorted(
            name
            for name, count in counts.items()
            if count > 1
        ),
        "official": sorted(
            actual.intersection(
                OFFICIAL_COMMANDS
            )
        ),
        "missing": sorted(
            set(OFFICIAL_COMMANDS)
            - actual
        ),
        "legacy": sorted(
            actual.intersection(
                LEGACY_COMMANDS
            )
        ),
    }


def log_report(report):
    LOG.info(
        "已註冊頂層指令數：%d",
        report["total"],
    )

    LOG.info(
        "正式指令：%s",
        "、".join(
            report["official"]
        ) or "無",
    )

    LOG.info(
        "尚未註冊的正式指令：%s",
        "、".join(
            report["missing"]
        ) or "無",
    )

    LOG.info(
        "仍顯示的舊指令：%s",
        "、".join(
            report["legacy"]
        ) or "無",
    )

    if report["duplicates"]:
        LOG.error(
            "頂層指令重複：%s",
            "、".join(
                report["duplicates"]
            ),
        )


def remove_character_legacy(tree):
    """移除六條已確認有替代入口的角色舊指令。"""

    names = {
        command.name
        for command in tree.get_commands()
    }

    removed = []

    for old_name, replacement in (
        CHARACTER_LEGACY_MAP.items()
    ):
        if replacement not in names:
            LOG.warning(
                "保留 /%s："
                "替代指令 /%s 尚未註冊",
                old_name,
                replacement,
            )
            continue

        command = tree.get_command(
            old_name
        )

        if command is None:
            LOG.info(
                "舊指令 /%s 已不存在",
                old_name,
            )
            continue

        if not isinstance(
            command,
            app_commands.Command,
        ):
            LOG.warning(
                "保留 /%s："
                "不是一般斜線指令",
                old_name,
            )
            continue

        result = tree.remove_command(
            old_name,
            type=discord.AppCommandType.chat_input,
        )

        if result is not None:
            removed.append(
                old_name
            )

            LOG.info(
                "已停用舊角色指令 /%s "
                "→ /%s",
                old_name,
                replacement,
            )

    LOG.info(
        "第一批舊角色指令停用數：%d",
        len(removed),
    )

    return removed


def remove_optional_legacy(tree):
    """可選清理；預設不移除金融舊入口。"""

    if not hide_legacy_enabled():
        LOG.info(
            "其他舊指令隱藏功能未啟用。"
        )
        return []

    names = {
        command.name
        for command in tree.get_commands()
    }

    # 額外檢查，避免沒有正式入口
    # 就隱藏仍在使用的功能。
    required = {
        "創角",
        "玩家申請",
        "銀行",
    }

    missing = required - names

    if missing:
        LOG.warning(
            "不執行第二批清理，"
            "尚缺：%s",
            "、".join(sorted(missing)),
        )
        return []

    removed = []

    for name in sorted(
        OPTIONAL_HIDE_COMMANDS
    ):
        command = tree.get_command(
            name
        )

        if not isinstance(
            command,
            app_commands.Command,
        ):
            continue

        old = tree.remove_command(
            name,
            type=discord.AppCommandType.chat_input,
        )

        if old is not None:
            removed.append(name)

    LOG.warning(
        "第二批停用 %d 條：%s",
        len(removed),
        "、".join(removed) or "無",
    )

    return removed


class RPBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.loaded_modules = []
        self.hidden_legacy = []
        self.command_report = {}

    async def setup_hook(self):
        LOG.info(
            "開始初始化 Discord RP 資料庫"
        )

        await asyncio.to_thread(
            init_db
        )

        extensions = discover_extensions()

        for extension in extensions:
            LOG.info(
                "正在載入：%s",
                extension,
            )

            try:
                await self.load_extension(
                    extension
                )

            except Exception:
                LOG.exception(
                    "模組載入失敗：%s",
                    extension,
                )
                raise

            self.loaded_modules.append(
                extension
            )

        LOG.info(
            "成功載入 %d 個模組",
            len(self.loaded_modules),
        )

        # 所有模組載入完才清理指令，
        # 防止後載入的模組重新註冊舊入口。
        self.hidden_legacy = (
            remove_character_legacy(
                self.tree
            )
        )

        self.hidden_legacy.extend(
            remove_optional_legacy(
                self.tree
            )
        )

        # 清理後重新盤點指令
        self.command_report = (
            audit_commands(
                self.tree
            )
        )

        log_report(
            self.command_report
        )

        if self.command_report["duplicates"]:
            raise RuntimeError(
                "頂層斜線指令存在重複名稱。"
            )

        # 第一步：同步全域指令。
        # Discord 會用目前清理後的清單
        # 更新全域指令，移除舊入口。
        synced_global = await self.tree.sync()

        LOG.info(
            "已完成全域同步：%d 條頂層指令",
            len(synced_global),
        )

        # 第二步：如果有指定測試伺服器，
        # 再同步該伺服器的指令清單。
        test_guild_id = get_test_guild_id()

        if test_guild_id is not None:
            guild = discord.Object(
                id=test_guild_id
            )

            self.tree.copy_global_to(
                guild=guild
            )

            synced_guild = (
                await self.tree.sync(
                    guild=guild
                )
            )

            LOG.info(
                "已同步測試伺服器 %s："
                "%d 條頂層指令",
                test_guild_id,
                len(synced_guild),
            )

        else:
            LOG.info(
                "未設定 TEST_GUILD_ID；"
                "不覆寫其他伺服器專用指令。"
            )

        LOG.info(
            "Discord 指令同步完成"
        )

    async def on_ready(self):
        if self.user is None:
            return

        LOG.info(
            "機器人已登入：%s（%s）",
            self.user,
            self.user.id,
        )

        LOG.info(
            "目前連接 %d 個伺服器",
            len(self.guilds),
        )


bot = RPBot()


@bot.tree.command(
    name="測試",
    description="確認 Discord RP 機器人運作",
)
async def test_command(
    interaction: discord.Interaction,
):
    await interaction.response.send_message(
        "✅ Discord RP 機器人正常運作。",
        ephemeral=True,
    )


@bot.tree.command(
    name="系統狀態",
    description="查看機器人的模組及指令狀態",
)
@app_commands.guild_only()
async def system_status(
    interaction: discord.Interaction,
):
    is_manager = (
        interaction.guild is not None
        and (
            interaction.user.id
            == interaction.guild.owner_id
            or interaction.permissions.manage_guild
            or interaction.permissions.administrator
        )
    )

    embed = discord.Embed(
        title="🤖 Discord RP｜系統狀態",
        description="機器人目前已正常連線。",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="已載入模組",
        value=str(
            len(bot.loaded_modules)
        ),
        inline=True,
    )

    embed.add_field(
        name="頂層指令",
        value=str(
            bot.command_report.get(
                "total",
                0,
            )
        ),
        inline=True,
    )

    if is_manager:
        report = bot.command_report

        embed.add_field(
            name="已註冊正式入口",
            value=(
                "、".join(
                    report.get(
                        "official",
                        [],
                    )
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="尚未註冊正式入口",
            value=(
                "、".join(
                    report.get(
                        "missing",
                        [],
                    )
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="仍存在的舊指令",
            value=(
                "、".join(
                    report.get(
                        "legacy",
                        [],
                    )
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="本次停用的舊指令",
            value=(
                "、".join(
                    bot.hidden_legacy
                )[:1024]
                or "無"
            ),
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    LOG.error(
        "斜線指令發生錯誤：%s",
        error,
        exc_info=(
            type(error),
            error,
            error.__traceback__,
        ),
    )

    message = (
        "⚠️ 操作未能完成，"
        "請檢查輸入內容。"
        "如果持續出錯，請通知管理員。"
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

    except discord.HTTPException:
        LOG.exception(
            "無法傳送錯誤提示"
        )


def main():
    bot.run(
        get_token(),
        log_handler=None,
    )


if __name__ == "__main__":
    main()
