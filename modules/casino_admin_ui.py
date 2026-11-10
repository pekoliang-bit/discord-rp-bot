"""娛樂城群主管理按鈕。非 Cog，不建立新斜線指令。"""
from __future__ import annotations

import asyncio
import logging
import discord

from modules import casino_ready_presets as presets

LOG = logging.getLogger(__name__)


async def _owner(
    interaction: discord.Interaction,
    *,
    respond=True,
) -> bool:
    allowed = (
        interaction.guild is not None
        and interaction.user.id == interaction.guild.owner_id
    )
    if not allowed and respond:
        await interaction.response.send_message(
            "⛔ 僅群主可以管理娛樂城獎池。",
            ephemeral=True,
        )
    return allowed


class CasinoAdminButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="娛樂城管理",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await _owner(interaction):
            return

        await interaction.response.send_message(
            "⚙️ 娛樂城遊戲管理（先安裝預設內容、再逐款核准開放；玩家對賭停用）",
            view=CasinoAdminView(interaction.user.id),
            ephemeral=True,
        )


class CasinoOwnerOnlyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(CasinoAdminButton())


class CasinoAdminView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if (
            interaction.user.id != self.owner_id
            or not await _owner(interaction)
        ):
            return False
        return True

    @discord.ui.button(
        label="安裝 17 款預設遊戲",
        emoji="📦",
        style=discord.ButtonStyle.primary,
    )
    async def install(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            result = await asyncio.to_thread(
                presets.install_all,
                interaction.guild_id,
                actor_id=interaction.user.id,
            )
            await interaction.followup.send(
                f"✅ 新增 {len(result['created'])} 款"
                "（全部保持關閉）；"
                f"既有 {len(result['existing_unchanged'])} 款不變。\n"
                "彩票另需建立期別；玩家對賭仍暫停。下一步請點「開放／停用獎池」逐款核准。",
                ephemeral=True,
            )
        except Exception:
            LOG.exception("娛樂城預設安裝失敗")
            await interaction.followup.send(
                "⚠️ 安裝失敗，請檢查日誌；"
                "沒有開放任何未核准遊戲。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="建立彩票期別", emoji="🎫", style=discord.ButtonStyle.secondary,
    )
    async def make_draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LotteryDrawModal())

    @discord.ui.button(
        label="開放／停用獎池",
        emoji="🎟️",
        style=discord.ButtonStyle.secondary,
    )
    async def toggle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        try:
            rows = await asyncio.to_thread(
                presets.manageable_games,
                interaction.guild_id,
            )

            if not rows:
                await interaction.response.send_message(
                    "尚無獎池，請先按「安裝 17 款預設遊戲」。",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                "選擇遊戲，再確認要開放或停用。彩票核准後仍須建立期別。",
                view=CasinoGameToggleView(
                    interaction.user.id,
                    rows,
                ),
                ephemeral=True,
            )
        except Exception:
            LOG.exception("獎池管理讀取失敗")
            await interaction.response.send_message(
                "⚠️ 無法取得獎池清單。",
                ephemeral=True,
            )


class LotteryDrawModal(discord.ui.Modal, title="建立彩票期別"):
    game_code = discord.ui.TextInput(label="彩票代碼", placeholder="lottery_daily / lottery_royal / lottery_dream", max_length=80)
    draw_code = discord.ui.TextInput(label="期別名稱", placeholder="例如 20261110-A", max_length=80)
    cutoff = discord.ui.TextInput(label="截止時間（含時區）", placeholder="2026-11-11T19:00:00+08:00", max_length=40)
    draw_at = discord.ui.TextInput(label="開獎時間（含時區）", placeholder="2026-11-11T20:00:00+08:00", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            from modules.casino_lottery import create_draw
            draw = await asyncio.to_thread(
                create_draw, guild_id=interaction.guild_id,
                game_code=str(self.game_code.value).strip(), draw_code=str(self.draw_code.value).strip(),
                cutoff_at=str(self.cutoff.value).strip(), draw_at=str(self.draw_at.value).strip(),
            )
            await interaction.followup.send(
                f"✅ 已建立彩票期別 #{draw['id']}。開獎及派獎仍需運作彩票維護排程。", ephemeral=True)
        except Exception as exc:
            LOG.exception("建立彩票期別失敗")
            await interaction.followup.send(f"⚠️ 建立失敗：{str(exc)[:1000]}", ephemeral=True)


class CasinoGameToggleView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        rows: list[dict],
    ):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.code = None

        selector = discord.ui.Select(
            placeholder="選擇要管理的遊戲",
            row=0,
            options=[
                discord.SelectOption(
                    label=game["name"][:90],
                    value=game["code"],
                    description=(
                        "已開放"
                        if game["enabled"]
                        else "尚未開放"
                    ),
                )
                for game in rows[:25]
            ],
        )

        async def select(interaction: discord.Interaction):
            self.code = selector.values[0]
            await interaction.response.send_message(
                f"已選擇 `{self.code}`，請確認要開放或停用。",
                ephemeral=True,
            )

        selector.callback = select
        self.add_item(selector)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if (
            interaction.user.id != self.owner_id
            or not await _owner(interaction)
        ):
            return False
        return True

    async def _toggle(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ):
        if not self.code:
            await interaction.response.send_message(
                "請先從下拉選單選擇遊戲。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            row = await asyncio.to_thread(
                presets.toggle_game,
                interaction.guild_id,
                self.code,
                actor_id=interaction.user.id,
                enabled=enabled,
            )
            await interaction.followup.send(
                f"✅ {row['name']}："
                f"{'已開放' if row['enabled'] else '已停用'}。",
                ephemeral=True,
            )
        except (ValueError, PermissionError) as exc:
            await interaction.followup.send(
                f"⚠️ 無法修改：{exc}",
                ephemeral=True,
            )
        except Exception:
            LOG.exception("娛樂城核准／停用失敗")
            await interaction.followup.send(
                "⚠️ 修改失敗，請查看日誌。",
                ephemeral=True,
            )

    @discord.ui.button(
        label="核准並開放",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def enable(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._toggle(interaction, True)

    @discord.ui.button(
        label="停用",
        emoji="⛔",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def disable(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await self._toggle(interaction, False)
