"""排行榜點數分配 Discord 互動面板；由 /查看 的角色詳情開啟。"""
from __future__ import annotations

import asyncio
import logging
import secrets

import discord

LOG = logging.getLogger(__name__)
from modules.leaderboard_points_catalog import available_targets
from modules.leaderboard_points_storage import get_balance
from modules.leaderboard_points_confirmation import create_request, confirm_request
from modules.action_professional_points import get_balance as get_professional_balance


class AmountModal(discord.ui.Modal, title="排行榜點數分配確認"):
    amount = discord.ui.TextInput(label="要分配的點數", placeholder="例如：5", max_length=8)

    def __init__(self, owner_id, guild_id, character_id, pool, category, target):
        super().__init__(timeout=180)
        self.owner_id = int(owner_id)
        self.guild_id = str(guild_id)
        self.character_id = int(character_id)
        self.pool = pool
        self.category = category
        self.target = target

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        issue = secrets.token_hex(4).upper()
        LOG.error(
            "點數表單未處理例外 [POINT-%s] guild=%s character=%s pool=%s target=%s",
            issue, self.guild_id, self.character_id, self.pool, self.target,
            exc_info=(type(error), error, error.__traceback__),
        )
        text = f"⚠️ 點數表單發生錯誤（POINT-{issue}），尚未確認扣點。請將編號交給管理員查看 Railway Logs。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            LOG.exception("點數表單錯誤訊息也無法傳送 [POINT-%s]", issue)

    async def on_submit(self, interaction: discord.Interaction):
        # 禁止其他人重播此表單；同時不要執行任何資料庫查詢才回應 Discord。
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("無權操作這個角色。", ephemeral=True)
            return

        issue = secrets.token_hex(4).upper()
        try:
            # 必須優先 ACK，否則 Discord 在資料庫操作期間可能直接顯示紅色錯誤。
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.HTTPException:
            LOG.exception("點數表單 ACK 失敗 [POINT-%s] guild=%s character=%s", issue,
                          self.guild_id, self.character_id)
            return

        try:
            amount = int(str(self.amount.value).strip())
            if amount <= 0:
                raise ValueError("點數必須大於零")
            request = await asyncio.to_thread(
                create_request,
                self.guild_id, self.character_id, self.owner_id,
                self.pool, self.category, self.target, amount,
            )
            info = request["preview"]
            confirmation = ConfirmView(self.owner_id, request["token"])
            content = (
                f"🎯 **點數分配預覽**\n"
                f"預計增加：**{self.target} +{amount}**\n"
                f"分配前可用：{info.get('balance_before', info.get('balance', '—'))}\n"
                f"確認後才會扣除點數。請在180秒內點擊確認。"
            )
            # 使用 modal defer 所建立的原始回應，避免又送一個獨立訊息。
            await interaction.edit_original_response(content=content, view=confirmation)
        except (ValueError, PermissionError, TypeError, KeyError) as exc:
            LOG.warning("點數分配被拒絕 [POINT-%s] guild=%s char=%s: %s",
                        issue, self.guild_id, self.character_id, exc)
            await self._report(interaction, f"⚠️ {exc}；尚未扣除點數。", issue)
        except Exception:
            LOG.exception(
                "點數分配建立確認請求失敗 [POINT-%s] guild=%s character=%s pool=%s category=%s target=%s",
                issue, self.guild_id, self.character_id, self.pool, self.category, self.target,
            )
            await self._report(
                interaction,
                "⚠️ 點數預覽失敗，尚未確認扣點。請交給管理員查看 Railway Logs。",
                issue,
            )

    @staticmethod
    async def _report(interaction, message, issue):
        message = f"{message}\n錯誤查詢編號：`POINT-{issue}`"
        try:
            await interaction.edit_original_response(content=message, view=None)
        except discord.HTTPException:
            LOG.exception("點數錯誤原回覆失敗 [POINT-%s]", issue)
            try:
                await interaction.followup.send(message, ephemeral=True)
            except discord.HTTPException:
                LOG.exception("點數錯誤備援回覆失敗 [POINT-%s]", issue)


class ConfirmView(discord.ui.View):
    def __init__(self, owner_id, token):
        super().__init__(timeout=180)
        self.owner_id = int(owner_id)
        self.token = token

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("這不是你的確認面板。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認分配", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(confirm_request, self.token, interaction.user.id)
        except (ValueError, PermissionError) as exc:
            await interaction.edit_original_response(content=f"⚠️ 分配未完成：{exc}", view=None)
            return
        except Exception:
            LOG.exception("點數確認扣點失敗: owner=%s", self.owner_id)
            await interaction.edit_original_response(
                content="⚠️ 分配時遇到資料庫或系統錯誤，請管理員查看 Railway Logs；請先重新整理餘額確認是否入帳。",
                view=None,
            )
            return
        await interaction.edit_original_response(
            content=f"✅ 點數分配完成：{result}\n請返回角色點數面板按『重新整理』查看新餘額。",
            view=None,
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        from modules.leaderboard_points_confirmation import cancel_request
        await interaction.response.defer()
        try:
            await asyncio.to_thread(cancel_request, self.token, interaction.user.id)
        except Exception:
            LOG.exception("取消點數分配失敗")
            await interaction.edit_original_response(content="⚠️ 取消時發生錯誤，請稍後確認點數餘額。", view=None)
            return
        await interaction.edit_original_response(content="已取消，未扣除點數。", view=None)


class TargetSelect(discord.ui.Select):
    def __init__(self, owner_id, guild_id, character_id, pool, category, names):
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.character_id = character_id
        self.pool = pool
        self.category = category
        super().__init__(placeholder="選擇要增加的屬性或技能", min_values=1, max_values=1,
                         options=[discord.SelectOption(label=name[:100], value=name)
                                  for name in names[:25]])

    async def callback(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("無權操作。", ephemeral=True)
            return
        try:
            # 必須在 Discord 的互動時限內立即回覆 modal；不能先 defer。
            modal = AmountModal(
                self.owner_id, self.guild_id, self.character_id,
                self.pool, self.category, self.values[0],
            )
            await interaction.response.send_modal(modal)
        except discord.HTTPException as exc:
            LOG.exception("無法開啟點數輸入視窗: %s", exc)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ 無法開啟輸入視窗，請重新使用『🎯 點數分配』開啟新面板。", ephemeral=True
                )
        except Exception:
            LOG.exception("點數選項回呼失敗: guild=%s character=%s", self.guild_id, self.character_id)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ 點數選項處理失敗，請管理員查看 Railway Logs；未扣除點數。", ephemeral=True
                )


class PointPanel(discord.ui.View):
    def __init__(self, owner_id, guild_id, character_id, pool="free", category="attribute"):
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.guild_id = str(guild_id)
        self.character_id = int(character_id)
        self.pool = pool
        self.category = category
        self._build()

    def _build(self):
        for item in list(self.children):
            if isinstance(item, TargetSelect):
                self.remove_item(item)
        targets = available_targets('skill' if self.pool == 'professional' else self.pool)
        names = targets["attributes" if self.category == "attribute" else "skills"]
        if names:
            self.add_item(TargetSelect(self.owner_id, self.guild_id,
                                       self.character_id, self.pool, self.category, names))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("這不是你的點數面板。", ephemeral=True)
            return False
        return True

    def _description(self):
        balances = get_balance(self.guild_id, self.character_id)
        professional = get_professional_balance(self.guild_id, self.character_id)
        pool_name = {'free':'自由點數','skill':'技能點數','professional':'專業點數'}[self.pool]
        return (f"🎯 **角色點數分配**\n自由點數：{balances['free']}｜"
                f"技能點數：{balances['skill']}｜專業點數：{professional}\n"
                f"目前：{pool_name}／"
                f"{'屬性' if self.category == 'attribute' else '技能'}")

    @discord.ui.button(label="自由點數", style=discord.ButtonStyle.secondary, row=1)
    async def free(self, interaction, button):
        self.pool = "free"; self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    @discord.ui.button(label="技能點數", style=discord.ButtonStyle.secondary, row=1)
    async def skill(self, interaction, button):
        self.pool = "skill"; self.category = "skill"; self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    @discord.ui.button(label="專業點數", style=discord.ButtonStyle.secondary, row=1)
    async def professional(self, interaction, button):
        self.pool = "professional"; self.category = "skill"; self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    @discord.ui.button(label="屬性", style=discord.ButtonStyle.secondary, row=2)
    async def attribute(self, interaction, button):
        self.pool = "free"; self.category = "attribute"; self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    @discord.ui.button(label="技能", style=discord.ButtonStyle.secondary, row=2)
    async def skills(self, interaction, button):
        self.category = "skill"; self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    @discord.ui.button(label="🔄 重新整理", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction, button):
        self._build()
        await interaction.response.edit_message(content=self._description(), view=self)

    async def on_error(self, interaction, error, item):
        LOG.error("點數面板互動失敗: guild=%s character=%s item=%s", self.guild_id,
                  self.character_id, getattr(item, "custom_id", "unknown"),
                  exc_info=(type(error), error, error.__traceback__))
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "⚠️ 點數面板操作未成功，請重新開啟面板，並請管理員查看 Railway Logs。", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "⚠️ 點數面板操作未成功，請管理員查看 Railway Logs。", ephemeral=True
            )


async def open_points_panel(interaction, character_id):
    panel = PointPanel(interaction.user.id, interaction.guild_id, character_id)
    await interaction.response.send_message(panel._description(), view=panel, ephemeral=True)
