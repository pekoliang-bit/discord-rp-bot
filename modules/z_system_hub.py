"""公開系統總覽與獨立群主指引。

此模組只註冊兩個指令，不為未完成模組建立佔位指令。
不修改既有資料表。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

CATEGORIES = {
    "🎭 角色與 NPC": [
        ("創角", "建立與送審角色"),
        ("查看", "查閱角色與 NPC 資料"),
    ],
    "🏦 經濟與組織": [
        ("銀行", "角色金融與公司帳戶"),
        ("商城", "購物、背包、稱號與道具"),
        ("公司", "公司、店鋪與相關合約"),
        ("陣營", "陣營及共同資產"),
    ],
    "🎪 任務與活動": [
        ("任務", "任務、新手指引與獎勵"),
        ("活動", "報名、參與、任務證明"),
        ("登記", "作品與相關紀錄登記"),
        ("行動", "RP 行動與結算"),
    ],
    "🎲 公開玩法": [
        ("排行", "公開排行榜"),
        ("抽獎", "抽獎活動"),
        ("娛樂城", "娛樂遊戲"),
    ],
    "🛡️ 申請與管理": [
        ("申請", "活動企劃、店鋪、金融、角色退場與檢舉"),
        ("審核", "全部申請案件集中審核"),
        ("官方", "官方內容建立與系統設定"),
        ("群主指引", "群主專用設定流程"),
        ("總覽", "公開資訊、活動與獎項查詢入口"),
    ],
}

OWNER_GUIDE = (
    "👑 **群主建置指引**\n\n"
    "① 確認 Railway Volume 與資料庫備份。\n"
    "② 確認 Discord 頻道、機器人身分組與權限。\n"
    "③ 進入 `/官方` 授權審核員及其他職務。\n"
    "④ 設定角色審核流程及專用討論區。\n"
    "⑤ 設定銀行、商城、活動及獎勵規則。\n"
    "⑥ 設定活動報名頻道、通知頻道與對應權限。\n"
    "⑦ 使用測試角色驗證：創角→審核→資產→申請→通知。\n"
    "⑧ 確認 `/總覽` 可看到公開內容，並檢查 Railway Logs。\n\n"
    "*這是設定清單，不會自動修改伺服器。*"
)


def command_names(bot):
    return {cmd.name for cmd in bot.tree.get_commands()}


class OverviewPicker(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="選擇查詢分類",
            options=[discord.SelectOption(label=k, value=k) for k in CATEGORIES],
            row=0,
        )

    async def callback(self, i: discord.Interaction):
        view = self.view
        await i.response.edit_message(
            embed=view.render(self.values[0]), view=view
        )


class OverviewView(discord.ui.View):
    def __init__(self, bot, user_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.add_item(OverviewPicker())

    async def interaction_check(self, i: discord.Interaction):
        if i.user.id != self.user_id:
            await i.response.send_message(
                "請自行使用 `/總覽` 開啟查詢面板。", ephemeral=True
            )
            return False
        return True

    def render(self, category=None):
        embed = discord.Embed(
            title="🌐 流光城｜系統總覽",
            description=(
                "公開指令與功能查詢。✅ 已註冊；▫️ 尚未註冊。\n"
                "新手教學移至 `/任務`；申請與審核各自獨立。\n"
                "*已註冊不等於已完成實際功能測試。*"
            ),
            color=discord.Color.blurple(),
        )
        current = command_names(self.bot)
        groups = (
            {category: CATEGORIES[category]}
            if category in CATEGORIES else CATEGORIES
        )
        for group, values in groups.items():
            lines = [
                f"{'✅' if cmd in current else '▫️'} `/{cmd}`｜{desc}"
                for cmd, desc in values
            ]
            embed.add_field(name=group, value="\n".join(lines), inline=False)
        embed.set_footer(text="角色、活動及獎項的公開資料面板將逐步接入")
        return embed

    @discord.ui.button(label="所有指令", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, i: discord.Interaction, button: discord.ui.Button):
        await i.response.edit_message(embed=self.render(), view=self)


class SystemHub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="總覽", description="查看所有公開系統入口與功能")
    @app_commands.guild_only()
    async def overview(self, i: discord.Interaction):
        view = OverviewView(self.bot, i.user.id)
        await i.response.send_message(
            embed=view.render(), view=view, ephemeral=True
        )

    @app_commands.command(name="群主指引", description="群主專用的系統建置步驟")
    @app_commands.guild_only()
    async def owner_guide(self, i: discord.Interaction):
        if i.guild is None or i.user.id != i.guild.owner_id:
            await i.response.send_message(
                "⛔ 這份指引僅供伺服器群主查看。", ephemeral=True
            )
            return
        await i.response.send_message(OWNER_GUIDE, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SystemHub(bot))
