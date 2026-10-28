
"""Discord RP｜官方系統只讀驗收。

指令：/官方檢查
僅限伺服器群主使用。
不修改角色、商品、金幣或資料庫內容。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection

LOG = logging.getLogger(__name__)

MODULES = (
    ("官方管理", "modules.official_center",
     "OfficialCenter"),
    ("官方商城", "modules.shop", "Shop"),
    ("商城管理", "modules.official_shop",
     "ShopManageButton"),
    ("金幣管理", "modules.official_test_money",
     "MoneyManageButton"),
    ("內容建立", "modules.content_builder",
     "ContentButton"),
    ("兌換審核", "modules.redeem_ui",
     "RedeemManageButton"),
    ("場景商店", "modules.scene_shops",
     "SceneShopButton"),
)

TABLES = (
    ("官方人員申請", "official_staff_applications"),
    ("官方人員授權", "official_staff_grants"),
    ("官方身分組", "official_role_config"),
    ("商城商品", "shop_products"),
    ("商城背包", "shop_inventory"),
    ("商城交易", "shop_events"),
    ("官方內容", "official_contents"),
    ("場景商店", "scene_shops"),
)

COMMANDS = (
    "官方",
    "商城",
    "創角",
    "查看",
    "審核",
)


def inspect_modules(bot):
    results = []

    for label, module_name, member in MODULES:
        try:
            module = importlib.import_module(
                module_name
            )

            if not hasattr(module, member):
                raise AttributeError(
                    f"缺少 {member}"
                )

            if member in ("OfficialCenter", "Shop"):
                if bot.get_cog(member) is None:
                    raise RuntimeError(
                        "Cog 尚未載入"
                    )

            results.append(
                (label, True, "")
            )

        except Exception as exc:
            LOG.warning(
                "官方模組檢查失敗：%s",
                label,
                exc_info=True,
            )

            results.append(
                (label, False, str(exc)[:100])
            )

    return results


def inspect_database():
    results = []

    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            ).fetchall()

            existing = {
                str(row[0])
                for row in rows
            }

    except (sqlite3.Error, OSError) as exc:
        return [
            (
                "資料庫連線",
                False,
                str(exc)[:100],
            )
        ]

    for label, table in TABLES:
        success = table in existing

        results.append(
            (
                label,
                success,
                "" if success else "缺少資料表",
            )
        )

    return results


def inspect_commands(bot):
    names = {
        cmd.name
        for cmd in bot.tree.get_commands()
    }

    return [
        (
            "/" + name,
            name in names,
            (
                ""
                if name in names
                else "指令尚未在本地註冊"
            ),
        )
        for name in COMMANDS
    ]


def inspect_permissions(guild):
    me = guild.me

    if me is None:
        return [
            (
                "機器人身分",
                False,
                "無法取得機器人成員資料",
            )
        ]

    permissions = me.guild_permissions

    checks = (
        ("查看頻道", permissions.view_channel),
        ("發送訊息", permissions.send_messages),
        ("管理身分組", permissions.manage_roles),
        ("建立討論串", permissions.create_public_threads),
        ("管理討論串", permissions.manage_threads),
    )

    return [
        (
            label,
            bool(success),
            "" if success else "權限不足",
        )
        for label, success in checks
    ]


def make_report(groups):
    lines = [
        "🛠️ **官方系統驗收報告**",
        "",
    ]

    total = 0
    passed = 0

    for heading, results in groups:
        lines.append(f"**{heading}**")

        for label, success, reason in results:
            total += 1

            if success:
                passed += 1

            icon = "✅" if success else "❌"

            lines.append(
                f"{icon} {label}"
            )

            if not success and reason:
                safe_reason = (
                    discord.utils.escape_mentions(
                        str(reason)
                    )
                )

                lines.append(
                    f"　{safe_reason[:95]}"
                )

        lines.append("")

    failed = total - passed

    lines.insert(
        2,
        f"通過：{passed}/{total}"
        f"｜未通過：{failed}\n"
    )

    if failed:
        lines.append(
            "⚠️ **目前條件尚未齊全**"
        )

    else:
        lines.append(
            "✅ 基礎檢查通過。"
        )

        lines.append(
            "仍需進行實際互動、"
            "各頻道權限與交易流程測試。"
        )

    lines.append(
        "\n本次檢查不會修改或刪除任何資料。"
    )

    return "\n".join(lines)


class OfficialCheck(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="官方檢查",
        description="檢查官方系統、資料庫與權限",
    )
    @app_commands.guild_only()
    async def official_check(
        self,
        interaction: discord.Interaction,
    ):
        guild = interaction.guild

        if (
            guild is None
            or guild.owner_id != interaction.user.id
        ):
            await interaction.response.send_message(
                "⛔ 只有群主可以使用官方檢查。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:
            modules = await asyncio.to_thread(
                inspect_modules,
                self.bot,
            )

            database = await asyncio.to_thread(
                inspect_database
            )

            commands_result = inspect_commands(
                self.bot
            )

            permissions = inspect_permissions(
                guild
            )

            groups = [
                ("模組檢查", modules),
                ("資料庫檢查", database),
                ("指令檢查", commands_result),
                ("機器人伺服器權限", permissions),
            ]

            report = make_report(groups)

            # Discord 單則訊息不得超過 2000 字
            while report:
                if len(report) <= 1900:
                    chunk = report
                    report = ""
                else:
                    pos = report.rfind(
                        "\n",
                        0,
                        1800,
                    )

                    if pos <= 0:
                        pos = 1800

                    chunk = report[:pos]
                    report = report[pos:].lstrip("\n")

                await interaction.followup.send(
                    chunk,
                    ephemeral=True,
                )

        except Exception:
            LOG.exception(
                "官方驗收執行失敗"
            )

            await interaction.followup.send(
                "⚠️ 系統驗收失敗，"
                "請檢查 Railway 日誌。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        OfficialCheck(bot)
    )

    LOG.info(
        "官方檢查指令已載入。"
    )
