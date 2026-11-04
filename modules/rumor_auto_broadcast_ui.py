
"""在 /官方 管理功能中心嵌入「定時傳聞」設定，不新增頂層斜線指令。"""
from __future__ import annotations

import logging
import discord
from discord.ext import commands, tasks

from core import rumor_auto_broadcast as auto

LOG = logging.getLogger(__name__)


def owner(i):
    return bool(
        i.guild
        and i.guild.owner_id == i.user.id
    )


def summary(gid):
    s = auto.settings(gid)

    channel = (
        f'<#{s["channel_id"]}>'
        if s["channel_id"]
        else "尚未設定"
    )

    state = "啟用" if s["enabled"] else "停用"

    next_due = (
        f'<t:{s["next_due"]}:F>'
        if s["enabled"] and s["next_due"]
        else "—"
    )

    return (
        f"📰 **定時傳聞管理**\n"
        f"發布頻道：{channel}\n"
        f"狀態：{state}\n"
        f"發布週期：每 8 小時一則\n"
        f"下一次：{next_due}\n"
        "傳聞正面 50／負面 50；"
        "角色必須已啟用且未關閉整體及對應分類的傳聞授權。\n"
        "所有內容均為遊戲內虛構傳聞，"
        "並非已證實的事實。"
    )


class ChannelModal(
    discord.ui.Modal,
    title="設定定時傳聞頻道"
):
    target = discord.ui.TextInput(
        label="頻道連結、ID 或頻道提及",
        max_length=150
    )

    async def on_submit(self, i):
        if not owner(i):
            return await i.response.send_message(
                "僅群主可設定。",
                ephemeral=True
            )

        try:
            cid = auto.parse_channel(
                self.target.value,
                i.guild_id
            )

            channel = i.guild.get_channel(
                int(cid)
            )

            if not isinstance(
                channel,
                discord.TextChannel
            ):
                raise ValueError(
                    "請選擇本伺服器的文字頻道"
                )

            me = i.guild.me

            if (
                not me
                or not channel.permissions_for(me).send_messages
                or not channel.permissions_for(me).view_channel
            ):
                raise ValueError(
                    "機器人沒有在此頻道檢視與發送訊息的權限"
                )

            auto.set_channel(
                i.guild_id,
                cid,
                i.user.id
            )

            await i.response.edit_message(
                content=(
                    "✅ 已綁定頻道，請另外按啟用。\n"
                    + summary(i.guild_id)
                ),
                view=RumorAutoView(i)
            )

        except (ValueError, PermissionError) as exc:
            await i.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True
            )


class RumorAutoView(discord.ui.View):
    def __init__(self, i):
        super().__init__(timeout=600)

        self.uid = i.user.id
        self.gid = i.guild_id

    async def interaction_check(self, i):
        if (
            not owner(i)
            or self.uid != i.user.id
            or self.gid != i.guild_id
        ):
            await i.response.send_message(
                "沒有權限操作此面板。",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="設定頻道",
        style=discord.ButtonStyle.primary,
        row=0
    )
    async def channel(self, i, b):
        await i.response.send_modal(
            ChannelModal()
        )

    @discord.ui.button(
        label="啟用／停用",
        style=discord.ButtonStyle.success,
        row=0
    )
    async def toggle(self, i, b):
        try:
            new_state = not auto.settings(
                i.guild_id
            )["enabled"]

            auto.set_enabled(
                i.guild_id,
                new_state,
                i.user.id
            )

            await i.response.edit_message(
                content=summary(i.guild_id),
                view=RumorAutoView(i)
            )

        except ValueError as exc:
            await i.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True
            )

    @discord.ui.button(
        label="重新整理",
        style=discord.ButtonStyle.secondary,
        row=0
    )
    async def refresh(self, i, b):
        await i.response.edit_message(
            content=summary(i.guild_id),
            view=RumorAutoView(i)
        )

    @discord.ui.button(
        label="返回官方管理",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, b):
        from modules.official_center import (
            AdminToolsView,
            management_text
        )

        view = AdminToolsView(i)

        await i.response.edit_message(
            content=management_text(view),
            view=view
        )


class RumorAutoManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📰 定時傳聞",
            style=discord.ButtonStyle.secondary,
            row=3
        )

    async def callback(self, i):
        if not owner(i):
            return await i.response.send_message(
                "僅群主可以設定自動傳聞。",
                ephemeral=True
            )

        await i.response.edit_message(
            content=summary(i.guild_id),
            view=RumorAutoView(i)
        )


class RumorAutoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        auto.initialize()
        auto.templates()
        self.auto_publish.start()

    async def cog_unload(self):
        self.auto_publish.cancel()

    @tasks.loop(minutes=1)
    async def auto_publish(self):
        import asyncio

        guild_ids = await asyncio.to_thread(
            auto.due_guild_ids
        )

        for gid in guild_ids:
            guild = self.bot.get_guild(
                int(gid)
            )

            if guild is None:
                continue

            try:
                entry = await asyncio.to_thread(
                    auto.claim,
                    gid
                )

                if entry is None:
                    continue

                # 發布前重新確認角色傳聞授權及排程狀態。
                if not await asyncio.to_thread(
                    auto.still_consented,
                    gid,
                    entry
                ):
                    continue

                channel = guild.get_channel(
                    int(entry["channel_id"])
                )

                if not isinstance(
                    channel,
                    discord.TextChannel
                ):
                    LOG.warning(
                        "傳聞發布頻道不可用 guild=%s channel=%s",
                        gid,
                        entry["channel_id"]
                    )
                    continue

                label = (
                    "正面"
                    if entry["polarity"] == "positive"
                    else "負面"
                )

                message = await channel.send(
                    (
                        f"📰 **娛樂圈傳聞｜"
                        f"{label}・未經證實**\n"
                        f'{entry["text"]}\n\n'
                        "*本訊息為伺服器內的虛構 RP 事件，"
                        "並非真實新聞。*"
                    ),
                    allowed_mentions=(
                        discord.AllowedMentions.none()
                    )
                )

                await asyncio.to_thread(
                    auto.record_post,
                    entry,
                    message.id
                )

            except Exception:
                LOG.exception(
                    "定時傳聞發布失敗 guild=%s",
                    gid
                )

    @auto_publish.before_loop
    async def before_auto_publish(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(
        RumorAutoCog(bot)
    )
