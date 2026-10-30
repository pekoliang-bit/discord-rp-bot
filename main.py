"""Discord RP 主程式｜正式指令管理版。

功能：
- 初始化 SQLite 資料庫
- 自動載入 modules 中全部功能模組
- 維持商城模組的特殊載入順序
- 自動停用六條重複的角色舊指令
- 停用已有正式替代入口的銀行舊指令
- 保留尚未整合的特殊功能指令
- 同步 Discord 全域指令
- 支援 TEST_GUILD_ID 測試伺服器同步
- 管理員系統狀態
- 完整錯誤紀錄

Railway 環境變數：
DISCORD_TOKEN
DATABASE_PATH=/app/data/bot.sqlite3
TEST_GUILD_ID（選填）

注意：
指令清理只影響斜線指令註冊，
不刪除角色、草稿、銀行紀錄或資料庫。
"""

from __future__ import annotations

import asyncio
import ast
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


def get_legacy_sync_guild_ids():
    """取得明確授權清理的伺服器 ID，不自動碰觸其他伺服器。"""
    raw = os.getenv("LEGACY_SYNC_GUILD_IDS", "").strip()
    if not raw:
        return []
    ids = []
    for item in raw.replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdecimal() or int(item) <= 0:
            raise ValueError("LEGACY_SYNC_GUILD_IDS 必須是以逗號分隔的 Discord 伺服器數字 ID。")
        if int(item) not in ids:
            ids.append(int(item))
    return ids


# ==================================================
# 正式指令
# ==================================================

OFFICIAL_COMMANDS = {
    "創角": "建立角色與草稿",
    "查看": "角色與 NPC 資訊",
    "銀行": "銀行及金融業務",
    "商城": "商城及背包",
    "公司": "公司經營",
    "陣營": "陣營管理",
    "任務": "任務中心",
    "登記": "作品及活動登記",
    "行動": "RP 行動",
    "排行": "排行榜",
    "抽獎": "抽獎活動",
    "娛樂城": "娛樂活動",
    "申請": "玩家申請、企劃與檢舉",
    "審核": "審核中心",
    "官方": "官方管理",
    "內容建立": "官方內容建立",
    "總覽": "公開資訊與所有功能導覽",
    "群主指引": "群主專用建置說明",
}


# ==================================================
# 舊指令盤點
# ==================================================

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
    "提款",
    "存錢",
    "轉帳",
    "定存",
    "定存解約",
    "貸款申請",
    "貸款還款",
    "貸款紀錄",
    "貸款審核",
    "貸款待審",
    "信用卡申請",
    "信用卡待審",
    "信用卡審核",
    "信用卡繳款",
    "信用卡",
    "黑卡申請",
    "黑卡待審",
    "黑卡審核",
    "黑卡",
    "銀行利率",
    "存款",
    "角色轉帳",
}


# ==================================================
# 第一批：角色舊指令
# ==================================================

CHARACTER_LEGACY_MAP = {
    "建立角色": "創角",
    "角色填寫": "創角",
    "角色送審": "創角",
    "感情設定": "創角",
    "角色檔案": "查看",
    "角色審核": "審核",
}


# ==================================================
# 第二批：銀行舊指令
# ==================================================

BANK_LEGACY_MAP = {
    "存款": "銀行",
    "提款": "銀行",
    "存錢": "銀行",
    "角色轉帳": "銀行",
    "轉帳": "銀行",
    "定存": "銀行",
    "定存解約": "銀行",

    "貸款申請": "銀行",
    "貸款還款": "銀行",
    "貸款紀錄": "銀行",
    "貸款審核": "審核",
    "貸款待審": "審核",

    "信用卡申請": "銀行",
    "信用卡繳款": "銀行",
    "信用卡": "銀行",
    "信用卡審核": "審核",
    "信用卡待審": "審核",

    "黑卡申請": "銀行",
    "黑卡": "銀行",
    "黑卡審核": "審核",
    "黑卡待審": "審核",
}


# ==================================================
# 模組載入順序
# ==================================================

PRIORITY_MODULES = (
    "modules.shop",
    "modules.player_shops",
    "modules.storefront",
    "modules.shop_integration",
)

# 群主審核政策必須最後載入
FINAL_MODULES = (
    "modules.zz_owner_review_policy",
)

# 舉報服務檔不屬於 Discord Extension；只能透過整合入口載入。
REPORT_EXTENSION = "modules.report_commands"
REPORT_HELPER_PREFIX = "report_"


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
        if path.name.startswith("_") or path.stem in {"test_character", "test_content_manager"}:
            continue

        # 僅將具有模組層級 setup() 的檔案當作 Discord extension。
        # 服務模組由其他 Cog import，不應直接交給 load_extension()。
        # 使用 AST 靜態檢查，不執行任何模組頂層程式碼。
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            # 不隱藏有語法問題的指令模組：讓正式載入階段顯示錯誤。
            LOG.error("模組無法靜態檢查，保留載入以顯示錯誤：%s (%s)", path.name, exc)
            extensions.append(f"modules.{path.stem}")
            continue

        has_setup = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "setup"
            for node in tree.body
        )
        if not has_setup:
            LOG.info("略過非 Discord 擴充模組：%s", path.name)
            continue

        extensions.append(f"modules.{path.stem}")

    normal = [
        name
        for name in extensions
        if name not in PRIORITY_MODULES
        and name not in FINAL_MODULES
    ]

    priority = [
        name
        for name in PRIORITY_MODULES
        if name in extensions
    ]

    final = [
        name
        for name in FINAL_MODULES
        if name in extensions
    ]

    ordered = normal + priority + final

    # 舉報整合入口在正式核心模組之後載入；本身不註冊同名指令。
    # 僅當檔案實際存在時啟用，讓尚未安裝舉報模組的部署也能運作。
    if (MODULES_DIR / "report_commands.py").is_file():
        ordered.append(REPORT_EXTENSION)

    if len(ordered) != len(set(ordered)):
        raise RuntimeError(
            "模組載入清單存在重複項目。"
        )

    return ordered


# ==================================================
# 指令盤點
# ==================================================

def collect_top_level_commands(tree):
    return list(
        tree.get_commands()
    )


def audit_commands(tree):
    """盤點目前註冊的頂層指令。"""

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


# ==================================================
# 清理角色舊指令
# ==================================================

def remove_character_legacy(tree):
    """移除已有正式替代入口的角色指令。"""

    names = {
        command.name
        for command in tree.get_commands()
    }

    removed = []

    for old_name, replacement in (
        CHARACTER_LEGACY_MAP.items()
    ):
        if replacement not in names:
            continue

        command = tree.get_command(old_name)

        if command is None:
            continue

        if not isinstance(
            command,
            (app_commands.Command, app_commands.Group),
        ):
            continue

        result = tree.remove_command(
            old_name,
            type=discord.AppCommandType.chat_input,
        )

        if result is not None:
            removed.append(old_name)


    return removed


# ==================================================
# 清理銀行舊指令
# ==================================================

def remove_optional_legacy(tree):
    """清理已整合的銀行舊入口。

    僅取消斜線指令註冊。
    不刪除原本的金融函式或資料庫。
    """

    names = {
        command.name
        for command in tree.get_commands()
    }

    removed = []

    for name, replacement in BANK_LEGACY_MAP.items():

        if replacement not in names:
            continue

        command = tree.get_command(name)

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


    return removed


def remove_requested_legacy(tree, *, allow_groups=False):
    """一般指令可逐步清理；群組必須等相依模組全部載入後清理。"""
    from modules.zzz_legacy_cleanup import destination_of, PROTECTED

    names = {
        command.name
        for command in tree.get_commands()
    }
    removed = []
    for name in tuple(names):
        if name in PROTECTED:
            continue
        destination = destination_of(name)
        if not destination:
            continue
        command = tree.get_command(name)
        if isinstance(command, app_commands.Group) and not allow_groups:
            continue
        if destination == "申請" and destination not in names:
            destination = "玩家申請"
        if destination not in names:
            continue
        if tree.remove_command(
            name,
            type=discord.AppCommandType.chat_input,
        ) is not None:
            removed.append(name)
    return removed


# ==================================================
# 機器人主體
# ==================================================

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

            # 在每個模組載入後清理舊入口，避免先撞上100條上限。
            for name in (
                remove_character_legacy(self.tree)
                + remove_optional_legacy(self.tree)
                + remove_requested_legacy(self.tree)
            ):
                if name not in self.hidden_legacy:
                    self.hidden_legacy.append(name)

        LOG.info(
            "成功載入 %d 個模組",
            len(self.loaded_modules),
        )

        # 所有功能模組載入後再清理舊指令。
        for name in (
            remove_character_legacy(self.tree)
            + remove_optional_legacy(self.tree)
            + remove_requested_legacy(self.tree, allow_groups=True)
        ):
            if name not in self.hidden_legacy:
                self.hidden_legacy.append(name)

        self.command_report = audit_commands(
            self.tree
        )

        log_report(
            self.command_report
        )

        if self.command_report["duplicates"]:
            raise RuntimeError(
                "頂層斜線指令存在重複名稱。"
            )

        # 全域指令同步
        synced_global = await self.tree.sync()

        LOG.info(
            "已完成全域同步：%d 條頂層指令",
            len(synced_global),
        )

        # 測試伺服器及明確指定的伺服器同步。
        # Discord 的全域指令同步不會移除先前留下的 guild-scoped 指令。
        test_guild_id = get_test_guild_id()
        target_ids = get_legacy_sync_guild_ids()
        if test_guild_id is not None and test_guild_id not in target_ids:
            target_ids.append(test_guild_id)

        for guild_id in target_ids:
            guild = discord.Object(id=guild_id)
            # 注意：會以目前正式全域指令取代此伺服器舊的專用指令。
            # 清空本地 scope，避免累積以前的 guild-scoped 指令。
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            synced_guild = await self.tree.sync(guild=guild)
            LOG.info(
                "已清理並同步伺服器 %s：%d 條頂層指令",
                guild_id, len(synced_guild),
            )

        if not target_ids:
            LOG.info(
                "未設定 TEST_GUILD_ID / LEGACY_SYNC_GUILD_IDS；"
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


# ==================================================
# 測試指令
# ==================================================

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


# ==================================================
# 系統狀態
# ==================================================

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


# ==================================================
# 全域錯誤處理
# ==================================================

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


# ==================================================
# 啟動
# ==================================================

def main():
    bot.run(
        get_token(),
        log_handler=None,
    )


if __name__ == "__main__":
    main()
