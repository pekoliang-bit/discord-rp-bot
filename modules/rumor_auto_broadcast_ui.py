"""在 /官方 管理功能中心嵌入「定時傳聞」設定，不新增頂層斜線指令。"""
from __future__ import annotations

import asyncio
import logging
import os

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

    jobs = auto.recent_job_status(gid)

    last = "；".join(
        f"{j['state']}（重試 {j['attempts']} 次）"
        for j in jobs[:3]
    ) or "尚無紀錄"

    return (
        "📰 **定時傳聞管理**\n"
        f"發布頻道：{channel}\n"
        f"狀態：{state}\n"
        "發布週期：每 8 小時一則\n"
        f"下一次：{next_due}\n"
        "傳聞正面 50／負面 50；"
        "角色必須已啟用且未關閉整體及對應分類的傳聞授權。\n"
        f"最近排程：{last}\n"
        "正式案件需群主已啟用分級、複雜度與獎懲規則。\n"
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


class AddRumorTemplateModal(discord.ui.Modal, title="新增傳聞模板"):
    polarity = discord.ui.TextInput(label="傳聞方向（正面／負面）", max_length=10)
    category = discord.ui.TextInput(label="分類（如：工作、生活）", max_length=40)
    content = discord.ui.TextInput(label="模板內文（必須使用 {角色}）",
        style=discord.TextStyle.paragraph, min_length=12, max_length=900)

    async def on_submit(self, i):
        if not owner(i):
            return await i.response.send_message("僅群主可新增傳聞模板。", ephemeral=True)
        value = self.polarity.value.strip()
        polarity = {"正面":"positive", "負面":"negative",
                    "positive":"positive", "negative":"negative"}.get(value)
        try:
            code = auto.add_template(i.guild_id, polarity, self.category.value,
                                     self.content.value, i.user.id)
        except Exception as e:
            LOG.warning("新增傳聞模板未完成：%s", e)
            return await i.response.send_message(f"⚠️ 新增失敗：{e}", ephemeral=True)
        await i.response.send_message(f"✅ 新增傳聞模板 {code}。原有100則未變更；新增模板已加入本伺服器定時抽取池。", ephemeral=True)


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

    @discord.ui.button(label="➕ 新增傳聞模板",style=discord.ButtonStyle.primary,row=1)
    async def add_template_button(self, i, b):
        await i.response.send_modal(AddRumorTemplateModal())

    @discord.ui.button(
        label="返回官方管理",
        style=discord.ButtonStyle.secondary,
        row=1
    )
    async def back(self, i, b):
        from modules.official_center import (
            AdminToolsView,
            management_text,
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
        from core.rumor_auto_case_bridge import (
            create_linked_case,
            reconcile_unlinked,
        )

        guild_ids = await asyncio.to_thread(
            auto.due_guild_ids
        )

        for gid in guild_ids:
            if self.bot.get_guild(int(gid)):
                try:
                    await asyncio.to_thread(
                        auto.claim,
                        gid,
                    )
                except Exception:
                    LOG.exception(
                        "傳聞排程建檔失敗 guild=%s",
                        gid,
                    )

        # 發送前持久化佇列；故障後保留重試。
        job_ids = await asyncio.to_thread(
            auto.pending_jobs
        )

        for job_id in job_ids:
            job = await asyncio.to_thread(
                auto.lease_job,
                job_id,
            )

            if not job:
                continue

            entry = job["entry"]
            gid = job["guild_id"]

            guild = self.bot.get_guild(int(gid))

            if guild is None:
                await asyncio.to_thread(
                    auto.mark_retry,
                    job_id,
                    "伺服器暫時無法存取",
                )
                continue

            try:
                # 未發布前，重新檢查角色授權。
                if (
                    not job["message_id"]
                    and not await asyncio.to_thread(
                        auto.still_consented,
                        gid,
                        entry,
                    )
                ):
                    await asyncio.to_thread(
                        auto.skip_job,
                        job_id,
                        "已停用或角色撤回授權",
                    )
                    continue

                channel = guild.get_channel(
                    int(entry["channel_id"])
                )

                if channel is None:
                    channel = await guild.fetch_channel(
                        int(entry["channel_id"])
                    )

                if not isinstance(
                    channel,
                    discord.TextChannel
                ):
                    raise ValueError(
                        "發布目標不是文字頻道"
                    )

                message_id = job["message_id"]

                if not message_id:
                    # 不將內部工作單 ID／判定標籤顯示給玩家。
                    # Discord 發送成功但資料庫未更新時，依正文及工作單建立時間重找。
                    body = f'📰 **娛樂圈傳聞**\n{entry["text"]}'
                    async for msg in channel.history(limit=100):
                        if (
                            msg.author.id == self.bot.user.id
                            and msg.content == body
                            and msg.created_at.timestamp() >= int(job['created_at']) - 2
                        ):
                            message_id = str(msg.id)
                            break
                    if not message_id:
                        msg = await channel.send(
                            body,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        message_id = str(msg.id)

                    await asyncio.to_thread(
                        auto.mark_sent,
                        job_id,
                        message_id,
                    )

                # 正式案件使用既有核准規則。
                rumor_id, reason = await asyncio.to_thread(
                    create_linked_case,
                    job_id,
                )

                if reason:
                    LOG.warning(
                        "傳聞已刊登，正式案件待設定 job=%s: %s",
                        job_id,
                        reason,
                    )

                await asyncio.to_thread(
                    auto.finish_job,
                    job_id,
                    rumor_id,
                )

            except Exception as exc:
                LOG.exception(
                    "定時傳聞發布或登錄失敗 job=%s",
                    job_id,
                )

                await asyncio.to_thread(
                    auto.mark_retry,
                    job_id,
                    exc,
                )

        # 補齊規則後，可回補案件而不重發訊息。
        if os.getenv(
            "RUMOR_AUTO_CASE_RECONCILE",
            "1",
        ) == "1":
            await asyncio.to_thread(
                reconcile_unlinked,
                20,
            )

    @auto_publish.before_loop
    async def before_auto_publish(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(
        RumorAutoCog(bot)
    )
