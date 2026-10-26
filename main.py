
import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from core.config import get_token, get_test_guild_id
from core.database import init_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("discord_rp_bot")

BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR / "modules"



def discover_extensions():
    """自動尋找模組，並依照依賴關係載入。"""

    if not MODULES_DIR.is_dir():
        raise RuntimeError(
            "找不到 modules 資料夾"
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

    # 這些模組需要依照指定順序載入。
    # shop 必須先建立 /管理 指令群組。
    priority = [
        "modules.shop",
        "modules.player_shops",
        "modules.storefront",
        "modules.shop_integration",
    ]

    # 先載入沒有特殊順序要求的模組。
    ordered = [
        name
        for name in extensions
        if name not in priority
    ]

    # 再依正確順序載入商城相關模組。
    for name in priority:
        if name in extensions:
            ordered.append(name)

    return ordered


class RPBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.loaded_modules = []

    async def setup_hook(self):
        """初始化資料庫、載入功能、同步指令。"""

        logger.info("初始化 Discord RP 資料庫")
        await asyncio.to_thread(init_db)

        extensions = discover_extensions()

        for extension in extensions:
            logger.info("正在載入：%s", extension)

            try:
                await self.load_extension(extension)
            except Exception:
                logger.exception(
                    "模組載入失敗：%s",
                    extension,
                )
                raise

            self.loaded_modules.append(extension)

        logger.info(
            "已載入 %s 個功能模組",
            len(self.loaded_modules),
        )

        test_guild_id = get_test_guild_id()

        if test_guild_id is not None:
            guild = discord.Object(id=test_guild_id)

            self.tree.copy_global_to(guild=guild)

            commands_synced = await self.tree.sync(
                guild=guild
            )

            logger.info(
                "測試伺服器 %s 已同步 %s 條指令",
                test_guild_id,
                len(commands_synced),
            )
        else:
            commands_synced = await self.tree.sync()

            logger.info(
                "全域已同步 %s 條指令",
                len(commands_synced),
            )

    async def on_ready(self):
        if self.user is None:
            return

        logger.info(
            "機器人已登入：%s（ID：%s）",
            self.user,
            self.user.id,
        )

        logger.info(
            "目前連接 %s 個 Discord 伺服器",
            len(self.guilds),
        )


bot = RPBot()


@bot.tree.command(
    name="測試",
    description="確認 Discord RP 機器人是否正常運作",
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
    description="查看目前已載入的功能系統",
)
async def system_status(
    interaction: discord.Interaction,
):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "請在 Discord 伺服器內使用此指令。",
            ephemeral=True,
        )
        return

    modules = bot.loaded_modules

    if modules:
        module_text = "\n".join(
            f"• {name.removeprefix('modules.')}"
            for name in modules
        )
    else:
        module_text = "尚未載入功能模組"

    embed = discord.Embed(
        title="🤖 Discord RP 機器人｜系統狀態",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="機器人狀態",
        value="✅ 已連線",
        inline=False,
    )

    embed.add_field(
        name="載入模組數量",
        value=str(len(modules)),
        inline=False,
    )

    embed.add_field(
        name="已載入模組",
        value=module_text[:1024],
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
    logger.error(
        "指令執行錯誤：%s",
        error,
        exc_info=(
            type(error),
            error,
            error.__traceback__,
        ),
    )

    message = (
        "⚠️ 操作未能完成，請稍後重試。"
        "若問題持續，請通知管理員。"
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
        logger.exception(
            "無法傳送指令錯誤提示"
        )


def main():
    token = get_token()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
