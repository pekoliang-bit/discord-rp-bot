"""Discord RP /官方 → 功能管理 → 🧪 測試系統。

所有測試管理、角色論壇設定及補建功能均為 Discord 按鈕或 Modal，
不額外註冊 Slash 指令；群主與 Administrator 可操作。
"""
from __future__ import annotations

import logging
import discord

LOG = logging.getLogger(__name__)


def allowed(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    member = interaction.user
    return bool(
        guild is not None
        and (member.id == guild.owner_id or getattr(member.guild_permissions, "administrator", False))
    )


async def require_allowed(interaction: discord.Interaction) -> bool:
    if allowed(interaction):
        return True
    await interaction.response.send_message("⛔ 僅群主或 Administrator 管理員可使用測試系統。", ephemeral=True)
    return False


def forum_cog(interaction: discord.Interaction):
    return interaction.client.get_cog("CharacterForums")


async def category_by_id(interaction: discord.Interaction, raw_id: str):
    raw_id = str(raw_id).strip()
    if not raw_id.isdecimal():
        raise ValueError("請填入 Discord 分類的純數字 ID。")
    channel_id = int(raw_id)
    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await interaction.guild.fetch_channel(channel_id)
        except discord.HTTPException as exc:
            raise ValueError("找不到這個分類，請確認 ID 及機器人權限。") from exc
    if not isinstance(channel, discord.CategoryChannel):
        raise ValueError("這不是 Discord 分類（Category），請使用分類的 ID，不是帖子／論壇 ID。")
    if channel.guild.id != interaction.guild.id:
        raise ValueError("分類必須在目前伺服器內。")
    return channel


class BetaCategoryModal(discord.ui.Modal, title="設定測試角色論壇分類"):
    category_id = discord.ui.TextInput(
        label="測試角色 Discord 分類 ID", default="1572210490707288114",
        placeholder="僅填 Category ID", max_length=24
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_allowed(interaction):
            return
        try:
            category = await category_by_id(interaction, str(self.category_id.value))
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        cog = forum_cog(interaction)
        if cog is None:
            return await interaction.response.send_message("⚠️ 角色論壇模組未載入。", ephemeral=True)
        # Existing service performs update and rebuild and sends the result.
        await cog.set_beta_category(interaction, category)


class FormalCategoriesModal(discord.ui.Modal, title="設定正式角色論壇分類"):
    female_id = discord.ui.TextInput(
        label="女性角色 Discord 分類 ID", default="1567786100460625972", max_length=24
    )
    male_id = discord.ui.TextInput(
        label="男性角色 Discord 分類 ID", default="1567786323773759588", max_length=24
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_allowed(interaction):
            return
        try:
            female = await category_by_id(interaction, str(self.female_id.value))
            male = await category_by_id(interaction, str(self.male_id.value))
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        cog = forum_cog(interaction)
        if cog is None:
            return await interaction.response.send_message("⚠️ 角色論壇模組未載入。", ephemeral=True)
        await cog.set_category(interaction, female, male)


class TestSystemView(discord.ui.View):
    def __init__(self, actor_id: int):
        super().__init__(timeout=600)
        self.actor_id = int(actor_id)
        # Reuse the existing guarded, double-confirmed whole-pool reset.
        from modules.beta_reset_admin import BetaResetButton
        reset = BetaResetButton()
        reset.row = 2
        self.add_item(reset)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id or not allowed(interaction):
            await interaction.response.send_message("⛔ 僅建立此面板的管理員可操作，請自行重新開啟 /官方。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🧪 測試論壇分類", style=discord.ButtonStyle.primary, row=0)
    async def test_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetaCategoryModal())

    @discord.ui.button(label="♀♂ 正式論壇分類", style=discord.ButtonStyle.secondary, row=0)
    async def formal_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FormalCategoriesModal())

    @discord.ui.button(label="🔎 論壇分類檢查", style=discord.ButtonStyle.secondary, row=1)
    async def check_forums(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = forum_cog(interaction)
        if cog is None:
            return await interaction.response.send_message("⚠️ 角色論壇模組未載入。", ephemeral=True)
        await cog.check_categories(interaction)

    @discord.ui.button(label="🧩 論壇／缺帖補建", style=discord.ButtonStyle.success, row=1)
    async def rebuild_forums(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = forum_cog(interaction)
        if cog is None:
            return await interaction.response.send_message("⚠️ 角色論壇模組未載入。", ephemeral=True)
        await cog.rebuild(interaction)

    @discord.ui.button(label="⬅️ 返回功能管理", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.official_center import AdminToolsView, management_text
        view = AdminToolsView(interaction)
        await interaction.response.edit_message(content=management_text(view), view=view)


class OfficialTestHubButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🧪 測試系統", style=discord.ButtonStyle.primary, row=2)

    async def callback(self, interaction: discord.Interaction):
        if not await require_allowed(interaction):
            return
        view = TestSystemView(interaction.user.id)
        await interaction.response.edit_message(
            content=(
                "🧪 **官方測試系統**\n"
                "所有測試管理功能集中在此，無需額外斜線指令。\n\n"
                "🎲 姓名池整池重置：恢復34個名字、重設當輪抽角序號；會停用目前測試角色。\n"
                "📂 測試論壇分類：不分男女，建立角色私人論壇及四個固定帖子。\n"
                "♀♂ 正式論壇分類：分別指定男女角色論壇位置。\n"
                "🔎 分類檢查：查看分類、容量與機器人權限。\n"
                "🧩 缺帖補建：檢查既有角色論壇，補上缺少的通知／詢問／劇情討論／機器人使用。\n\n"
                "⚠️ 重置姓名池會改動角色資料，論壇補建不會重新抽角。"
            ),
            view=view,
        )
