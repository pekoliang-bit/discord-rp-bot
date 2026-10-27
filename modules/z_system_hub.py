
"""RP 正式指令框架和 /總覽。

不修改現有資料庫及既有指令。
尚未開發的指令顯示「開發中」。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

LOG = logging.getLogger(__name__)

CATEGORIES = {
    "🎭 角色與 NPC": [
        ("創角", "建立角色、草稿、配點與送審"),
        ("查看", "查看角色與 NPC 檔案"),
        ("角色切換", "切換、管理與刪除角色"),
    ],
    "🏦 銀行與商城": [
        ("銀行", "存提款、轉帳、信用卡、貸款、定存"),
        ("商城", "購物、背包、贈送、使用與寄售"),
    ],
    "🏢 組織與合約": [
        ("公司", "公司成立、職位、經營及金庫"),
        ("陣營", "加入、退出、貢獻及組織 Buff"),
        ("合約", "簽署、履約、續約與解約"),
    ],
    "🎬 演藝與任務": [
        ("任務", "官方、日常、特殊、限時、自製及個人回報"),
        ("登記", "作品、活動及履歷登記"),
        ("行動", "頻道行為及數值自動結算"),
    ],
    "🏆 排名與活動": [
        ("排行", "完整榜單每頁 20 人及個人榜單"),
        ("頒獎", "提名、評選、頒獎與榮譽"),
        ("抽獎", "參與抽獎與獎池查詢"),
        ("娛樂城", "娛樂遊戲及活動紀錄"),
    ],
    "🛡️ 申請與管理": [
        ("玩家申請", "店鋪、金融及特殊申請"),
        ("審核", "角色雙審及一般申請審核"),
        ("紀律", "所有舉報、調查、處分與申訴"),
        ("官方", "授權、經濟與系統規則設定"),
        ("內容建立", "官方任務、通告、劇情、活動與物品"),
    ],
    "📚 導覽": [
        ("總覽", "系統功能、導入指引及新手教學"),
    ],
}

PLAYER_GUIDE = (
    "🌱 **玩家新手指引**\n\n"
    "**① `/創角`**：建立角色草稿並填寫個人資料。\n"
    "**② 主攻**：從演繹、舞蹈、歌唱、綜藝、主持選一項；沒有副賽道。\n"
    "**③ 配點**：一般池與藝能池各 500 點，完成 13 項屬性。\n"
    "**④ 送審**：提交兩階段審核，通過後依通知確認啟用。\n"
    "**⑤ `/查看`**：確認角色資料，`/角色切換` 管理多角色。\n"
    "**⑥ `/銀行`、`/商城`**：使用目前已上線的金錢與購物功能。\n"
    "**⑦ `/任務`**：等正式任務系統上線後接取與回報。\n\n"
    "*標示開發中的指令目前不能進行實際操作。*"
)

ADMIN_GUIDE = (
    "📘 **管理員系統導入指引**\n\n"
    "**① 資料保存**：確認 Railway Volume 掛載及 `DATABASE_PATH`，"
    "並建立獨立備份。\n"
    "**② Discord 權限**：確認機器人可使用斜線指令、"
    "發送訊息、嵌入連結。\n"
    "**③ 身分與審核**：透過 `/官方` 建立授權，"
    "測試 `/審核` 的權限。\n"
    "**④ 經濟規則**：設定貨幣、利率、字數結算範圍與倍率。\n"
    "**⑤ RP 頻道**：確認哪些討論串允許結算、任務回報和行動。\n"
    "**⑥ 新手驗收**：建立草稿→配點→送審→啟用→查看資料。\n"
    "**⑦ 部署前**：先驗證新入口正常，再逐步清理舊指令。\n\n"
    "*這是操作指引，並非一鍵初始化；"
    "未開發的設定不會假裝已生效。*"
)


def installed_names(bot):
    return {
        command.name
        for command in bot.tree.get_commands()
    }


class OverviewSelect(discord.ui.Select):
    def __init__(self, hub):
        self.hub = hub

        super().__init__(
            placeholder="選擇系統分類",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=category,
                    value=category,
                )
                for category in CATEGORIES
            ],
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.edit_message(
            embed=self.hub.make_embed(
                self.values[0]
            ),
            view=OverviewView(
                self.hub,
                interaction.user.id,
            ),
        )


class OverviewView(discord.ui.View):
    def __init__(self, hub, owner_id):
        super().__init__(timeout=600)

        self.hub = hub
        self.owner_id = owner_id

        self.add_item(
            OverviewSelect(hub)
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "請自己使用 `/總覽` 開啟介面。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="🌱 玩家新手指引",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def player_guide(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            PLAYER_GUIDE,
            ephemeral=True,
        )

    @discord.ui.button(
        label="📘 管理員導入指引",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def admin_guide(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        permissions = getattr(
            interaction.user,
            "guild_permissions",
            None,
        )

        if not (
            permissions
            and permissions.manage_guild
        ):
            await interaction.response.send_message(
                "⛔ 只有具有「管理伺服器」權限的"
                "人員可檢視管理員指引。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            ADMIN_GUIDE,
            ephemeral=True,
        )

    @discord.ui.button(
        label="🏠 全部系統",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.edit_message(
            embed=self.hub.make_embed(),
            view=OverviewView(
                self.hub,
                self.owner_id,
            ),
        )


class SystemHub(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.placeholders = set()

    def make_embed(self, category=None):
        embed = discord.Embed(
            title="🤖 RP 機器人・系統總覽",
            description=(
                "✅ 已註冊既有指令（仍需功能實測）\n"
                "🛠️ 開發中（暫時不可使用）\n\n"
                "選擇分類查看詳細功能。"
            ),
            color=discord.Color.blurple(),
        )

        categories = (
            {category: CATEGORIES[category]}
            if category in CATEGORIES
            else CATEGORIES
        )

        registered = installed_names(
            self.bot
        )

        for title, items in categories.items():
            lines = []

            for name, info in items:
                if name in self.placeholders:
                    status = "🛠️"
                elif name in registered:
                    status = "✅"
                else:
                    status = "⚠️"

                lines.append(
                    f"{status} `/{name}` — {info}"
                )

            embed.add_field(
                name=title,
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(
            text=(
                "已註冊 ≠ 通過實測；"
                "總覽只顯示本次機器人狀態"
            )
        )

        return embed

    @app_commands.command(
        name="總覽",
        description="查看系統入口、開發進度與新手指引",
    )
    @app_commands.guild_only()
    async def overview(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.send_message(
            embed=self.make_embed(),
            view=OverviewView(
                self,
                interaction.user.id,
            ),
            ephemeral=True,
        )


def make_placeholder(name, description):
    async def callback(
        interaction: discord.Interaction,
    ):
        await interaction.response.send_message(
            f"🛠️ **/{name} 尚在開發中**\n"
            f"{description}\n\n"
            "目前此入口不會更改資料、"
            "發放獎勵或進行扣款。"
            "可透過 `/總覽` 查看其他系統。",
            ephemeral=True,
        )

    return app_commands.Command(
        name=name,
        description=(
            "開發中｜" + description
        )[:100],
        callback=callback,
    )


async def setup(bot: commands.Bot):
    cog = SystemHub(bot)

    await bot.add_cog(cog)

    for items in CATEGORIES.values():
        for name, description in items:
            # /商城 由依賴排序中的 shop
            # 模組較晚載入，必須預留名稱。
            if (
                name in ("總覽", "商城")
                or name in installed_names(bot)
            ):
                continue

            try:
                bot.tree.add_command(
                    make_placeholder(
                        name,
                        description,
                    )
                )

                cog.placeholders.add(name)

                LOG.info(
                    "已建立開發中指令：/%s",
                    name,
                )

            except app_commands.CommandAlreadyRegistered:
                LOG.info(
                    "/%s 已由其他模組註冊，"
                    "略過佔位指令",
                    name,
                )
