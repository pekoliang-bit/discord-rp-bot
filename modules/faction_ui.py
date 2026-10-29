
"""Discord RP 陣營互動介面。

檔案位置：modules/faction_ui.py

唯一斜線指令：
    /陣營

功能：
- 陣營首頁
- 查看五個預設陣營
- 查看陣營經費與內部排行榜
- 選擇自己的角色
- 替角色加入初始陣營
- 向所屬陣營供奉金幣
- 查看商店及經費規則

不建立其他陣營斜線指令。
"""

from __future__ import annotations

import discord

from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.economy import EconomyError

from modules import faction_core
from modules.faction_donations import donate


def owned_characters(guild_id, user_id):
    """查詢玩家擁有的已啟用原創角色。"""

    with get_connection() as db:

        return db.execute(
            """
            SELECT
                c.id,
                c.name,
                m.faction_id
            FROM characters AS c
            LEFT JOIN faction_members AS m
              ON m.guild_id = c.guild_id
             AND m.character_id = c.id
            WHERE c.guild_id = ?
              AND c.owner_id = ?
              AND c.status = 'active'
              AND c.character_type = 'player'
            ORDER BY c.id
            LIMIT 25
            """,
            (
                str(guild_id),
                str(user_id),
            ),
        ).fetchall()


def home_text(guild_id):
    """產生陣營首頁文字。"""

    factions = faction_core.list_factions(
        guild_id
    )

    lines = []

    for _, name, pool in factions:

        safe_name = discord.utils.escape_markdown(
            str(name)
        )

        lines.append(
            f"• **{safe_name}**"
            f"｜經費池 {pool:,} 金幣"
        )

    faction_text = "\n".join(lines)

    return (
        "**⚔️ 陣營中心**\n\n"
        f"{faction_text}\n\n"
        "每個角色獨立選擇陣營。\n"
        "每 100 金幣有效入池資金 = 1 積分。\n"
        "退出或轉換陣營的違約金"
        "會進入原陣營經費池，但不計積分。"
    )


class PrivateView(discord.ui.View):
    """限制面板只能由開啟者操作。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(timeout=180)

        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "這不是你的陣營操作面板，"
                "請自行使用 /陣營。",
                ephemeral=True,
            )

            return False

        return True


class FactionDetail(PrivateView):
    """陣營資訊與內部排行榜。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        factions = faction_core.list_factions(
            guild_id
        )

        choices = [
            discord.SelectOption(
                label=name[:100],
                value=str(fid),
            )
            for fid, name, _ in factions
        ][:25]

        selector = discord.ui.Select(
            placeholder="選擇要查看的陣營",
            options=choices,
        )

        selector.callback = self.show

        self.selector = selector
        self.add_item(selector)

    async def show(self, interaction):

        try:

            info = faction_core.snapshot(
                self.guild_id,
                int(self.selector.values[0]),
            )

        except ValueError as exc:

            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )

            return

        rank_lines = []

        for index, entry in enumerate(
            info["rank"],
            start=1,
        ):

            cid, name, points = entry

            safe_name = (
                discord.utils.escape_markdown(
                    str(name)
                )
            )

            rank_lines.append(
                f"{index}. {safe_name}"
                f"（角色 #{cid}）"
                f" — {points:,} 分"
            )

        rank = (
            "\n".join(rank_lines)
            if rank_lines
            else "本期尚無有效貢獻。"
        )

        safe_faction_name = (
            discord.utils.escape_markdown(
                str(info["name"])
            )
        )

        await interaction.response.edit_message(
            content=(
                f"**{safe_faction_name}**\n\n"
                f"經費池："
                f"{info['pool']:,} 金幣\n"
                f"目前第 {info['period']} 期\n\n"
                "**內部排行榜**\n"
                f"{rank}"
            ),
            view=self,
        )

    @discord.ui.button(
        label="返回陣營首頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content=home_text(
                self.guild_id
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )


class JoinFaction(PrivateView):
    """角色初次加入陣營的選單。"""

    def __init__(
        self,
        guild_id,
        user_id,
        character_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        self.character_id = character_id

        factions = faction_core.list_factions(
            guild_id
        )

        choices = [
            discord.SelectOption(
                label=name[:100],
                value=str(fid),
            )
            for fid, name, _ in factions
        ][:25]

        selector = discord.ui.Select(
            placeholder="替角色選擇初始陣營",
            options=choices,
        )

        selector.callback = self.join

        self.selector = selector
        self.add_item(selector)

    async def join(self, interaction):

        try:

            faction_core.join(
                self.guild_id,
                self.character_id,
                int(self.selector.values[0]),
                self.user_id,
            )

        except ValueError as exc:

            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                f"角色 #{self.character_id}"
                " 已成功加入陣營！\n\n"
                + home_text(self.guild_id)
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="返回角色選擇",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content="請選擇要加入陣營的角色：",
            view=CharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )


class CharacterSelection(PrivateView):
    """玩家選擇自己名下的角色。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        rows = owned_characters(
            guild_id,
            user_id,
        )

        if rows:

            options = []

            for row in rows:

                status = (
                    "已有陣營"
                    if row["faction_id"] is not None
                    else "尚未加入"
                )

                options.append(
                    discord.SelectOption(
                        label=str(
                            row["name"]
                        )[:90],
                        description=(
                            f"角色 #{row['id']}"
                            f"｜{status}"
                        ),
                        value=str(row["id"]),
                    )
                )

            selector = discord.ui.Select(
                placeholder="選擇自己的角色",
                options=options,
            )

            selector.callback = self.choose

            self.selector = selector
            self.add_item(selector)

    async def choose(self, interaction):

        cid = int(
            self.selector.values[0]
        )

        rows = owned_characters(
            self.guild_id,
            self.user_id,
        )

        row = next(
            (
                item
                for item in rows
                if int(item["id"]) == cid
            ),
            None,
        )

        if row is None:

            await interaction.response.send_message(
                "角色已無法操作，"
                "請重新開啟 /陣營。",
                ephemeral=True,
            )

            return

        if row["faction_id"] is not None:

            await interaction.response.send_message(
                "此角色已加入陣營。\n"
                "轉換或退出需要正式違約流程，"
                "目前尚未開放。",
                ephemeral=True,
            )

            return

        safe_name = (
            discord.utils.escape_markdown(
                str(row["name"])
            )
        )

        await interaction.response.edit_message(
            content=(
                f"替 **{safe_name}**"
                f"（#{cid}）選擇陣營："
            ),
            view=JoinFaction(
                self.guild_id,
                self.user_id,
                cid,
            ),
        )

    @discord.ui.button(
        label="返回陣營首頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content=home_text(
                self.guild_id
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )


class DonationModal(
    discord.ui.Modal,
    title="向所屬陣營供奉",
):
    """供奉金額輸入表單。"""

    amount = discord.ui.TextInput(
        label="供奉金額（金幣）",
        placeholder="例如：12500",
        max_length=16,
        required=True,
    )

    def __init__(
        self,
        guild_id,
        user_id,
        character_id,
    ):
        super().__init__()

        self.guild_id = guild_id
        self.user_id = user_id
        self.character_id = character_id

    async def on_submit(
        self,
        interaction,
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "只能操作自己的角色。",
                ephemeral=True,
            )

            return

        try:

            amount = int(
                str(self.amount.value).strip()
            )

        except ValueError:

            await interaction.response.send_message(
                "請輸入正整數金額。",
                ephemeral=True,
            )

            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:

            result = donate(
                self.guild_id,
                self.character_id,
                self.user_id,
                amount,
                str(interaction.id),
            )

        except (
            ValueError,
            EconomyError,
        ) as exc:

            await interaction.followup.send(
                f"供奉失敗：{exc}",
                ephemeral=True,
            )

            return

        if result["duplicated"]:

            message = (
                "這筆交易已經處理過，"
                "沒有重複扣款。"
            )

        else:

            message = "供奉成功！"

        await interaction.followup.send(
            (
                f"**{message}**\n"
                f"經費入池："
                f"{result['amount']:,} 金幣\n"
                f"本次增加積分："
                f"{result['points']:,}"
            ),
            ephemeral=True,
        )


class DonationCharacterSelection(PrivateView):
    """選擇要供奉經費的角色。"""

    def __init__(
        self,
        guild_id,
        user_id,
    ):
        super().__init__(
            guild_id,
            user_id,
        )

        rows = [
            row
            for row in owned_characters(
                guild_id,
                user_id,
            )
            if row["faction_id"] is not None
        ]

        if rows:

            options = [
                discord.SelectOption(
                    label=str(row["name"])[:90],
                    description=(
                        f"角色 #{row['id']}"
                    ),
                    value=str(row["id"]),
                )
                for row in rows
            ]

            selector = discord.ui.Select(
                placeholder="選擇要供奉的角色",
                options=options,
            )

            selector.callback = self.choose

            self.selector = selector
            self.add_item(selector)

    async def choose(self, interaction):

        cid = int(
            self.selector.values[0]
        )

        rows = owned_characters(
            self.guild_id,
            self.user_id,
        )

        valid = any(
            int(row["id"]) == cid
            and row["faction_id"] is not None
            for row in rows
        )

        if not valid:

            await interaction.response.send_message(
                "角色不符合供奉資格。",
                ephemeral=True,
            )

            return

        await interaction.response.send_modal(
            DonationModal(
                self.guild_id,
                self.user_id,
                cid,
            )
        )

    @discord.ui.button(
        label="返回陣營首頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content=home_text(
                self.guild_id
            ),
            view=FactionHome(
                self.guild_id,
                self.user_id,
            ),
        )


class FactionHome(PrivateView):
    """統一的 /陣營 首頁。"""

    @discord.ui.button(
        label="我的角色／加入陣營",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def join(
        self,
        interaction,
        button,
    ):

        rows = owned_characters(
            self.guild_id,
            self.user_id,
        )

        if not rows:

            await interaction.response.send_message(
                "目前沒有已啟用的原創角色。",
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                "請選擇要加入陣營的角色。\n"
                "已有陣營的角色不可重複加入。"
            ),
            view=CharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="陣營資訊／內部排行",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def info(
        self,
        interaction,
        button,
    ):

        await interaction.response.edit_message(
            content="請選擇要查看的陣營：",
            view=FactionDetail(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="供奉陣營經費",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def donate_button(
        self,
        interaction,
        button,
    ):

        rows = [
            row
            for row in owned_characters(
                self.guild_id,
                self.user_id,
            )
            if row["faction_id"] is not None
        ]

        if not rows:

            await interaction.response.send_message(
                "沒有已加入陣營的角色。",
                ephemeral=True,
            )

            return

        await interaction.response.edit_message(
            content=(
                "請選擇要供奉的角色。\n"
                "供奉會從角色錢包扣款，"
                "並增加所屬陣營的經費。"
            ),
            view=DonationCharacterSelection(
                self.guild_id,
                self.user_id,
            ),
        )

    @discord.ui.button(
        label="商店與經費說明",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def shop(
        self,
        interaction,
        button,
    ):

        await interaction.response.send_message(
            (
                "**🛍️ 陣營商店與經費**\n\n"
                "**一般商店**\n"
                "玩家使用個人資金購買"
                "道具與個人加成。\n\n"
                "**專屬商店**\n"
                "當期第一名與第二名"
                "可使用陣營經費，"
                "購買全員正向加成。\n\n"
                "**暗害與防護**\n"
                "相關行動需要支付資金，"
                "詳細規則尚待實作。\n\n"
                "目前商店交易尚未開放，"
                "不會扣除任何資金。"
            ),
            ephemeral=True,
        )


class FactionCog(commands.Cog):
    """陣營系統唯一斜線指令。"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="陣營",
        description=(
            "開啟陣營中心："
            "角色加入、經費、排行榜與商店"
        ),
    )
    @app_commands.guild_only()
    async def faction(
        self,
        interaction: discord.Interaction,
    ):

        await interaction.response.send_message(
            content=home_text(
                interaction.guild_id
            ),
            view=FactionHome(
                interaction.guild_id,
                interaction.user.id,
            ),
            ephemeral=True,
        )


async def setup(bot):
    """Discord Cog 載入入口。"""

    faction_core.initialize()

    await bot.add_cog(
        FactionCog(bot)
    )
