
"""Discord RP 機器人主程式。

負責：
1. 初始化資料庫
2. 依照依賴順序載入模組
3. 檢查斜線指令名稱衝突
4. 檢查正式指令入口的整合進度
5. 同步 Discord 斜線指令
6. 記錄啟動及錯誤日誌
7. 提供機器人測試與系統狀態查詢

此檔案不直接刪除任何既有功能指令。
"""

from __future__ import annotations

import asyncio
import logging
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


# 正式規劃的統一玩家入口。
# 這份清單用來檢查整合進度，
# 不會自動建立尚未實作的指令。

OFFICIAL_COMMANDS = {
    "創角": "角色建立與資料填寫",
    "查看": "公開與私人角色檔案",
    "角色切換": "選擇目前使用角色",
    "玩家申請": "統一玩家申請中心",
    "審核": "統一官方審核中心",
    "官方": "官方管理與權限中心",
    "銀行": "銀行與金融服務",
    "商城": "官方商城與玩家商店",
    "登記": "作品與活動登記",
    "轉帳": "角色轉帳",
    "提款": "銀行提款",
    "存錢": "銀行存款",
}


# 尚待整合的舊版指令。
# 只記錄，不會刪除或隱藏。

LEGACY_COMMANDS = {
    "建立角色",
    "角色",
    "角色填寫",
    "角色檔案",
    "角色送審",
    "角色審核",
    "角色啟用",
    "初始配點",
    "屬性",
    "屬性清單",
    "設定官方屬性",
    "感情設定",
    "追蹤數值",
    "測試角建立",
    "貸款申請",
    "貸款審核",
    "貸款還款",
    "貸款紀錄",
    "貸款待審",
    "信用卡申請",
    "信用卡待審",
    "信用卡審核",
    "信用卡繳款",
    "信用卡銷戶",
    "黑卡申請",
    "黑卡待審",
    "黑卡審核",
    "黑卡通知",
    "店鋪申請",
    "商城新增",
    "商城修改",
    "商城下架",
    "銀行利率",
    "定存解約",
    "角色轉帳",
    "存款",
}


# 這些模組有彼此依賴，
# 必須依序載入。

PRIORITY_MODULES = (
    "modules.shop",
    "modules.player_shops",
    "modules.storefront",
    "modules.shop_integration",
)


def discover_extensions() -> list[str]:
    """探索所有正式模組並安排載入順序。"""

    if not MODULES_DIR.is_dir():
        raise RuntimeError(
            "找不到 modules 資料夾。"
        )

    discovered = []

    for path in sorted(
        MODULES_DIR.glob("*.py")
    ):
        if path.stem.startswith("_"):
            continue

        discovered.append(
            f"modules.{path.stem}"
        )

    normal = [
        name
        for name in discovered
        if name not in PRIORITY_MODULES
    ]

    priority = [
        name
        for name in PRIORITY_MODULES
        if name in discovered
    ]

    ordered = normal + priority

    if len(ordered) != len(set(ordered)):
        raise RuntimeError(
            "發現重複載入的功能模組。"
        )

    return ordered


def command_names(tree) -> list[str]:
    """取得目前註冊的全域頂層指令。"""

    return [
        command.name
        for command in tree.get_commands()
    ]


def inspect_commands(tree) -> dict:
    """檢查正式入口、舊入口與名稱衝突。"""

    names = command_names(tree)
    counts = Counter(names)

    duplicates = sorted(
        name
        for name, count in counts.items()
        if count > 1
    )

    available = set(names)

    official_ready = sorted(
        available.intersection(
            OFFICIAL_COMMANDS
        )
    )

    official_missing = sorted(
        set(OFFICIAL_COMMANDS)
        - available
    )

    legacy_active = sorted(
        available.intersection(
            LEGACY_COMMANDS
        )
    )

    return {
        "total": len(names),
        "duplicates": duplicates,
        "official_ready": official_ready,
        "official_missing": official_missing,
        "legacy_active": legacy_active,
    }


def log_command_report(report: dict):
    """在 Railway 日誌列出檢查結果。"""

    LOG.info(
        "目前註冊 %s 條頂層斜線指令",
        report["total"],
    )

    if report["duplicates"]:
        LOG.error(
            "指令名稱重複：%s",
            "、".join(report["duplicates"]),
        )

    LOG.info(
        "已存在正式入口：%s",
        (
            "、".join(
                report["official_ready"]
            )
            or "尚無"
        ),
    )

    LOG.info(
        "尚未實作的正式入口：%s",
        (
            "、".join(
                report["official_missing"]
            )
            or "無"
        ),
    )

    LOG.info(
        "仍在使用的舊指令：%s",
        (
            "、".join(
                report["legacy_active"]
            )
            or "無"
        ),
    )


class RPBot(commands.Bot):
    """Discord RP 主機器人。"""

    def __init__(self):
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.loaded_modules = []
        self.command_report = {}

    async def setup_hook(self):
        """啟動初始化與指令同步。"""

        LOG.info("開始初始化資料庫")

        await asyncio.to_thread(
            init_db
        )

        extensions = discover_extensions()

        for extension in extensions:
            LOG.info(
                "載入功能模組：%s",
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

                # 核心功能載入失敗時停止啟動，
                # 避免部分指令運作而產生錯帳。
                raise

            self.loaded_modules.append(
                extension
            )

        LOG.info(
            "成功載入 %s 個功能模組",
            len(self.loaded_modules),
        )

        report = inspect_commands(
            self.tree
        )

        self.command_report = report

        log_command_report(
            report
        )

        if report["duplicates"]:
            raise RuntimeError(
                "斜線指令存在名稱衝突："
                + "、".join(
                    report["duplicates"]
                )
            )

        test_guild_id = get_test_guild_id()

        if test_guild_id is not None:
            guild = discord.Object(
                id=test_guild_id
            )

            # 將全域指令複製到測試伺服器。
            self.tree.copy_global_to(
                guild=guild
            )

            commands_synced = (
                await self.tree.sync(
                    guild=guild
                )
            )

            LOG.info(
                "測試伺服器 %s 已同步 %s 條指令",
                test_guild_id,
                len(commands_synced),
            )

        else:
            commands_synced = (
                await self.tree.sync()
            )

            LOG.info(
                "已全域同步 %s 條指令",
                len(commands_synced),
            )

    async def on_ready(self):
        if self.user is None:
            return

        LOG.info(
            "機器人已登入：%s｜%s",
            self.user,
            self.user.id,
        )

        LOG.info(
            "目前連接 %s 個 Discord 伺服器",
            len(self.guilds),
        )


bot = RPBot()


@bot.tree.command(
    name="測試",
    description="確認 Discord RP 機器人是否運作",
)
async def test_command(
    interaction: discord.Interaction,
):
    await interaction.response.send_message(
        "✅ Discord RP 機器人已成功回應！",
        ephemeral=True,
    )


@bot.tree.command(
    name="系統狀態",
    description="查看機器人載入狀態",
)
@app_commands.guild_only()
async def system_status(
    interaction: discord.Interaction,
):
    modules = bot.loaded_modules
    report = bot.command_report

    embed = discord.Embed(
        title="🤖 Discord RP｜系統狀態",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="連線狀態",
        value="✅ 已連線",
        inline=False,
    )

    embed.add_field(
        name="已載入模組",
        value=str(len(modules)),
        inline=True,
    )

    embed.add_field(
        name="頂層指令數量",
        value=str(
            report.get("total", 0)
        ),
        inline=True,
    )

    loaded_text = "\n".join(
        "• "
        + name.removeprefix("modules.")
        for name in modules
    )

    embed.add_field(
        name="模組清單",
        value=(
            loaded_text[:1024]
            or "尚未載入"
        ),
        inline=False,
    )

    # 一般玩家不需要看到開發中的
    # 指令清單及內部整合資訊。
    is_admin = bool(
        interaction.permissions.administrator
        or interaction.permissions.manage_guild
    )

    if is_admin:
        ready = report.get(
            "official_ready",
            [],
        )

        missing = report.get(
            "official_missing",
            [],
        )

        legacy = report.get(
            "legacy_active",
            [],
        )

        embed.add_field(
            name="已存在的正式入口",
            value=(
                "、".join(ready)[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="待完成的正式入口",
            value=(
                "、".join(missing)[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="尚待整合的舊指令",
            value=(
                "、".join(legacy)[:1024]
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
        "⚠️ 操作未能完成。"
        "請稍後重試；若持續發生，"
        "請通知管理員。"
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
    token = get_token()

    bot.run(
        token,
        log_handler=None,
    )


if __name__ == "__main__":
    main()
