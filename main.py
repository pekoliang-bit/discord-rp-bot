
"""Discord RP 主程式｜正式指令管理版。

功能：
- 初始化資料庫
- 自動載入所有 modules 模組
- 維持商城模組的特殊載入順序
- 盤點正式指令及舊指令
- 偵測指令重複
- 選擇性隱藏已整合的舊入口
- 同步 Discord 指令
- 顯示系統狀態

Railway 設定：
RP_HIDE_LEGACY_COMMANDS=0  預設保留所有舊指令
RP_HIDE_LEGACY_COMMANDS=1  隱藏已列入安全清單的舊指令

重要：
啟用隱藏設定之前，必須先完成新入口實測。
此功能只移除 Discord 指令註冊，
不刪除資料庫、不刪除 Python 模組及核心函式。
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


# 已規劃的正式玩家／管理入口。
# 這只是檢查清單，不會建立空指令。
OFFICIAL_COMMANDS = {
    "創角": "完整角色建立",
    "查看": "角色資訊查詢",
    "角色切換": "目前使用角色",
    "玩家申請": "玩家申請中心",
    "審核": "官方審核中心",
    "官方": "官方管理中心",
    "銀行": "銀行服務",
    "商城": "商城服務",
    "登記": "演藝作品登記",
    "存錢": "銀行存款",
    "提款": "銀行提款",
    "轉帳": "角色轉帳",
}


# 可能可以由新入口取代的舊指令。
# 這個集合僅用於報表。
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


# 經新入口確實可用後，才允許隱藏的舊指令。
# 若仍需要舊指令操作，保持關閉即可。
#
# 特意不包含：
# 角色啟用、貸款還款、信用卡繳款、定存、
# 定存解約、信用卡銷戶等獨立必要功能。
#
# 也不隱藏 /管理 群組中的商城管理子指令。
OPTIONAL_HIDE_COMMANDS = {
    "建立角色",
    "角色填寫",
    "角色送審",
    "初始配點",
    "店鋪申請",
    "貸款申請",
    "信用卡申請",
    "黑卡申請",
    "存款",
    "角色轉帳",
}


# 原始專案存在商城指令群組的依賴順序。
# 保持不變，避免 /管理 指令群組損壞。
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

    return raw in (
        "1",
        "true",
        "yes",
        "on",
    )


def discover_extensions() -> list[str]:
    """從 modules 目錄找出所有正式功能模組。"""

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
            "模組載入清單有重複項目。"
        )

    return ordered


def collect_top_level_commands(tree):
    return list(
        tree.get_commands()
    )


def audit_commands(tree):
    """取得目前已註冊的實際指令狀態。"""

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
        "已註冊頂層指令數：%s",
        report["total"],
    )

    LOG.info(
        "正式指令：%s",
        "、".join(
            report["official"]
        ) or "無",
    )

    LOG.info(
        "尚未建立的正式指令：%s",
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
            "發現重複的頂層指令：%s",
            "、".join(
                report["duplicates"]
            ),
        )


def remove_legacy_commands(tree):
    """只移除明確允許隱藏的全域頂層指令。

    使用者仍可重新關閉環境變數並重新同步，
    恢復這些舊指令。
    """

    if not hide_legacy_enabled():
        LOG.info(
            "舊指令隱藏功能未啟用，"
            "目前保留所有既有指令。"
        )
        return []

    # 確認必要的新入口均存在後才隱藏舊入口。
    names = {
        command.name
        for command in tree.get_commands()
    }

    dependencies = {
        "創角",
        "玩家申請",
        "存錢",
        "轉帳",
    }

    missing = dependencies - names

    if missing:
        raise RuntimeError(
            "無法隱藏舊指令；"
            "新入口尚未完整註冊："
            + "、".join(sorted(missing))
        )

    removed = []

    for name in sorted(
        OPTIONAL_HIDE_COMMANDS
    ):
        command = tree.get_command(name)

        if command is None:
            continue

        if not isinstance(
            command,
            app_commands.Command,
        ):
            # 不移除指令群組。
            continue

        old = tree.remove_command(
            name,
            type=discord.AppCommandType.chat_input,
        )

        if old is not None:
            removed.append(name)

    LOG.warning(
        "已隱藏 %s 條舊指令：%s",
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
            "成功載入 %s 個模組",
            len(self.loaded_modules),
        )

        self.hidden_legacy = (
            remove_legacy_commands(
                self.tree
            )
        )

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
                "頂層斜線指令名稱重複。"
            )

        test_guild_id = (
            get_test_guild_id()
        )

        if test_guild_id is not None:
            guild = discord.Object(
                id=test_guild_id
            )

            self.tree.copy_global_to(
                guild=guild
            )

            synced = await self.tree.sync(
                guild=guild
            )

            LOG.info(
                "已同步測試伺服器 %s："
                "%s 條頂層指令",
                test_guild_id,
                len(synced),
            )

            LOG.warning(
                "目前採測試伺服器同步模式；"
                "既有全域指令不會自動清除。"
            )

        else:
            synced = await self.tree.sync()

            LOG.info(
                "已完成全域同步："
                "%s 條頂層指令",
                len(synced),
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
            "目前連接 %s 個伺服器",
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
        interaction.user.id
        == interaction.guild.owner_id
        or interaction.permissions.manage_guild
        or interaction.permissions.administrator
    )

    embed = discord.Embed(
        title="🤖 Discord RP｜系統狀態",
        description="機器人目前已正常連線。",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="已載入模組",
        value=str(len(bot.loaded_modules)),
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
            name="已存在的正式入口",
            value=(
                "、".join(
                    report.get("official", [])
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="待建立的正式入口",
            value=(
                "、".join(
                    report.get("missing", [])
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="仍在使用的舊指令",
            value=(
                "、".join(
                    report.get("legacy", [])
                )[:1024]
                or "無"
            ),
            inline=False,
        )

        embed.add_field(
            name="本次隱藏的舊指令",
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
        "請檢查輸入內容，"
        "若持續發生請通知管理員。"
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
