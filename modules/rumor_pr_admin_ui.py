"""/官方 管理功能中心使用的群主公關策略按鈕；不註冊新指令。"""
from __future__ import annotations

import discord
from core.rumor_pr_strategy_admin import list_strategies, update_strategy


def _owner(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.guild.owner_id == interaction.user.id)


async def _deny(interaction: discord.Interaction):
    message = "只有伺服器群主可以管理正式公關策略。"
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def _summary(guild_id: int) -> str:
    items = list_strategies(guild_id)
    lines = ["📣 **公關策略管理**", "僅群主能新增、修改、啟用或停用策略。", ""]
    if not items:
        lines.append("目前沒有已設定的策略；不會自動填入預設分數。")
    for item in items[:20]:
        lines.append(
            f"• {item.strategy}｜{item.base_score} 分｜"
            f"{'啟用' if item.enabled else '停用'}｜v{item.version}"
        )
    if len(items) > 20:
        lines.append(f"另有 {len(items)-20} 項未顯示。")
    return "\n".join(lines)[:1900]


class PRStrategyModal(discord.ui.Modal, title="設定公關策略"):
    strategy = discord.ui.TextInput(label="策略名稱", max_length=80, required=True)
    score = discord.ui.TextInput(label="官方基礎分數（0～10000）", max_length=5, required=True)
    enabled = discord.ui.TextInput(label="啟用或停用", placeholder="啟用", max_length=2, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not _owner(interaction):
            await _deny(interaction)
            return
        try:
            raw = str(self.score.value).strip()
            if not raw.isdecimal():
                raise ValueError("分數必須為整數")
            state = str(self.enabled.value).strip()
            if state not in ("啟用", "停用"):
                raise ValueError("狀態必須填『啟用』或『停用』")
            result = update_strategy(
                interaction.guild_id, str(self.strategy.value).strip(),
                int(raw), state == "啟用", interaction.user.id, trusted=True,
            )
        except (ValueError, PermissionError, RuntimeError) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ {result.strategy}：{result.base_score} 分，"
            f"{'啟用' if result.enabled else '停用'}，版本 v{result.version}",
            ephemeral=True,
        )


class PRStrategyView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.user.id != self.owner_id
                or interaction.guild_id != self.guild_id
                or not _owner(interaction)):
            await _deny(interaction)
            return False
        return True

    @discord.ui.button(label="新增／修改策略", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PRStrategyModal())

    @discord.ui.button(label="重新整理", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=_summary(self.guild_id), view=self)


    @discord.ui.button(label="⬅️ 返回管理功能中心", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.official_center import AdminToolsView, management_text
        view = AdminToolsView(interaction)
        await interaction.response.edit_message(content=management_text(view), view=view)


class PRStrategyManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📣 公關策略", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction):
        if not _owner(interaction):
            await _deny(interaction)
            return
        try:
            content = _summary(interaction.guild_id)
        except Exception:
            # 初始化不完整時拒絕開啟，不改動任何策略資料。
            await interaction.response.send_message(
                "⚠️ 公關策略資料表尚未初始化，請檢查資料庫啟動程序。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            content, view=PRStrategyView(interaction), ephemeral=True,
        )
