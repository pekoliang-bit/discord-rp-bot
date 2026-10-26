
"""Discord RP：統一審核中心——角色審核。

正式指令：/審核

目前負責完整的角色雙階段審核：
- 待審角色清單
- 第一審及第二審
- 查看角色送審資料
- 通過、退件及退件原因
- 禁止審核自己的角色
- 禁止同一人連續完成雙審
- 使用現有審核核心及審核日誌

其他申請類型後續接入同一入口，
不另建重複的審核指令。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
    require_admin,
    review_character,
)
from modules.profile import (
    FIELDS,
    read_settings,
    missing_fields,
)

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25
VIEW_TIMEOUT = 600

REVIEW_STATES = {
    "review_1": "第一階段審核",
    "review_2": "第二階段審核",
}


def get_actor(interaction):
    actor = Actor.from_interaction(interaction)
    require_admin(actor)
    return actor


def verify_user(interaction, original_actor):
    if (
        interaction.guild_id is None
        or str(interaction.guild_id)
        != original_actor.guild_id
        or str(interaction.user.id)
        != original_actor.user_id
    ):
        raise PermissionDenied(
            "這不是你的審核操作介面。"
        )

    return get_actor(interaction)


def get_pending(actor, page=0):
    """查詢待審角色，使用資料庫分頁。"""

    require_admin(actor)

    page = max(0, int(page))

    with get_connection() as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM characters
            WHERE guild_id = ?
              AND character_type = 'player'
              AND status IN ('review_1','review_2')
            """,
            (actor.guild_id,),
        ).fetchone()["total"]

        max_page = max(
            0,
            (count - 1) // PAGE_SIZE,
        )

        page = min(page, max_page)

        rows = conn.execute(
            """
            SELECT id, name, age, occupation,
                   owner_id, status
            FROM characters
            WHERE guild_id = ?
              AND character_type = 'player'
              AND status IN ('review_1','review_2')
            ORDER BY
                CASE status
                    WHEN 'review_2' THEN 0
                    ELSE 1
                END,
                id ASC
            LIMIT ? OFFSET ?
            """,
            (
                actor.guild_id,
                PAGE_SIZE,
                page * PAGE_SIZE,
            ),
        ).fetchall()

    return {
        "items": [dict(row) for row in rows],
        "total": count,
        "page": page,
        "max_page": max_page,
    }


def get_review_character(actor, character_id):
    """每次操作重新讀取角色當前狀態。"""

    require_admin(actor)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM characters
            WHERE guild_id = ?
              AND id = ?
              AND character_type = 'player'
            """,
            (
                actor.guild_id,
                int(character_id),
            ),
        ).fetchone()

    if row is None:
        raise InvalidState(
            "找不到這名角色。"
        )

    if row["status"] not in REVIEW_STATES:
        raise InvalidState(
            "角色已不在待審狀態，請重新整理。"
        )

    return dict(row)


def get_review_progress(actor, character_id):
    with get_connection() as conn:
        try:
            row = conn.execute(
                """
                SELECT *
                FROM character_review_progress
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.guild_id,
                    int(character_id),
                ),
            ).fetchone()

        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise

    return dict(row) if row else None


def list_text(result):
    return (
        "🛡️ **官方審核中心｜角色申請**\n\n"
        f"目前待審：{result['total']} 名\n"
        f"第 {result['page'] + 1}/"
        f"{result['max_page'] + 1} 頁\n\n"
        "請選擇要審核的角色。\n"
        "角色審核分為兩階段，"
        "必須由不同審核者完成。"
    )


def safe_display(value, maximum=500):
    if value in ("", None):
        return "未填寫"

    if isinstance(value, (list, dict)):
        value = json.dumps(
            value,
            ensure_ascii=False,
        )

    value = str(value)
    return value[:maximum]


def make_review_embed(actor, character_id):
    character = get_review_character(
        actor,
        character_id,
    )

    settings = read_settings(character)
    progress = get_review_progress(
        actor,
        character_id,
    )

    state = REVIEW_STATES[
        character["status"]
    ]

    embed = discord.Embed(
        title=f"📋 角色審核 #{character_id}",
        description=(
            f"**{safe_display(character['name'], 100)}**\n"
            f"目前階段：{state}\n"
            f"申請人：<@{character['owner_id']}>"
        ),
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="基本資料",
        value=(
            f"年齡：{character['age']}\n"
            f"職業：{safe_display(character['occupation'], 150)}\n"
            f"角色類型：原創角色"
        ),
        inline=False,
    )

    # Discord Embed 每個欄位最多 1024 字；
    # 將資料拆分，避免背景文字太長無法傳送。
    lines = []
    chunks = []

    for label, key in FIELDS.items():
        if key in ("name", "age", "occupation"):
            continue

        value = settings.get(key)

        line = (
            f"**{label}**："
            f"{safe_display(value, 260)}"
        )

        lines.append(line)

    for label, key in (
        ("生日", "birthday"),
        ("感情狀態", "relationship_status"),
        ("伴侶", "partner_name"),
    ):
        lines.append(
            f"**{label}**："
            f"{safe_display(settings.get(key), 150)}"
        )

    chunk = ""

    for line in lines:
        if len(chunk) + len(line) + 1 > 1000:
            chunks.append(chunk)
            chunk = ""

        chunk += line + "\n"

    if chunk:
        chunks.append(chunk)

    # 最多保留 15 個檔案欄位，避免超過 Embed 限制。
    for index, content in enumerate(chunks[:15]):
        embed.add_field(
            name=(
                "角色設定"
                if index == 0
                else f"角色設定（續 {index + 1}）"
            ),
            value=content,
            inline=False,
        )

    if progress:
        first = progress.get("first_reviewer")

        embed.add_field(
            name="審核進度",
            value=(
                f"第一審："
                f"{'<@' + str(first) + '>' if first else '未完成'}\n"
                "第二審："
                + (
                    "待進行"
                    if character["status"] == "review_2"
                    else "尚未開始"
                )
            ),
            inline=False,
        )

    missing = missing_fields(character)

    if missing:
        embed.add_field(
            name="⚠️ 尚缺必填資料",
            value="、".join(missing)[:1000],
            inline=False,
        )

    embed.set_footer(
        text="審核操作將寫入官方審核紀錄"
    )

    return embed


async def send_error(interaction, exc):
    message = f"⚠️ {str(exc)[:1700]}"

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True,
        )


class AuthorizedView(discord.ui.View):
    def __init__(self, actor):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.actor = actor

    async def interaction_check(self, interaction):
        try:
            verify_user(interaction, self.actor)
            return True

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)
            return False


class ReviewSelect(discord.ui.Select):
    def __init__(self, actor, result):
        self.actor = actor

        options = []

        for character in result["items"]:
            stage = REVIEW_STATES.get(
                character["status"],
                "未知狀態",
            )

            options.append(
                discord.SelectOption(
                    label=(
                        f"#{character['id']} "
                        f"{character['name']}"
                    )[:100],
                    description=(
                        f"{stage}｜"
                        f"{character['occupation'] or '未設定職業'}"
                    )[:100],
                    value=str(character["id"]),
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有待審角色",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇待審角色",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not bool(result["items"]),
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            character_id = int(self.values[0])

            embed = make_review_embed(
                self.actor,
                character_id,
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=ReviewDetailView(
                    self.actor,
                    character_id,
                ),
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
        ) as exc:
            await send_error(interaction, exc)


class ReviewListView(AuthorizedView):
    def __init__(self, actor, page=0):
        super().__init__(actor)

        result = get_pending(
            actor,
            page,
        )

        self.page = result["page"]
        self.max_page = result["max_page"]

        self.add_item(
            ReviewSelect(
                actor,
                result,
            )
        )

        self.previous.disabled = self.page <= 0
        self.next.disabled = (
            self.page >= self.max_page
        )

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(
        self,
        interaction,
        button,
    ):
        result = get_pending(
            self.actor,
            self.page - 1,
        )

        await interaction.response.edit_message(
            content=list_text(result),
            embed=None,
            view=ReviewListView(
                self.actor,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="下一頁 ➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(
        self,
        interaction,
        button,
    ):
        result = get_pending(
            self.actor,
            self.page + 1,
        )

        await interaction.response.edit_message(
            content=list_text(result),
            embed=None,
            view=ReviewListView(
                self.actor,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="🔄 重新整理",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def refresh(
        self,
        interaction,
        button,
    ):
        result = get_pending(
            self.actor,
            self.page,
        )

        await interaction.response.edit_message(
            content=list_text(result),
            embed=None,
            view=ReviewListView(
                self.actor,
                result["page"],
            ),
        )


class RejectModal(discord.ui.Modal):
    def __init__(self, actor, character_id):
        super().__init__(
            title="角色審核｜退件"
        )

        self.actor = actor
        self.character_id = character_id

        self.reason = discord.ui.TextInput(
            label="退件原因（必填）",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
            placeholder="請具體說明需要修改的內容",
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction):
        try:
            actor = verify_user(
                interaction,
                self.actor,
            )

            result = await asyncio.to_thread(
                review_character,
                actor,
                self.character_id,
                approve=False,
                reason=str(self.reason.value),
            )

            await interaction.response.send_message(
                (
                    "✅ **角色已退回草稿**\n\n"
                    f"角色編號：#{self.character_id}\n"
                    f"結果：{result}\n"
                    f"原因：{self.reason.value}"
                ),
                ephemeral=True,
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await send_error(interaction, exc)


class ReviewDetailView(AuthorizedView):
    def __init__(self, actor, character_id):
        super().__init__(actor)
        self.character_id = character_id

    @discord.ui.button(
        label="✅ 通過本階段",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def approve(
        self,
        interaction,
        button,
    ):
        try:
            actor = verify_user(
                interaction,
                self.actor,
            )

            # 在執行前重新確認狀態，
            # 防止兩名審核員操作過期面板。
            get_review_character(
                actor,
                self.character_id,
            )

            result = await asyncio.to_thread(
                review_character,
                actor,
                self.character_id,
                approve=True,
                reason="",
            )

            await interaction.response.edit_message(
                content=(
                    "✅ **審核已完成**\n\n"
                    f"角色：#{self.character_id}\n"
                    f"結果：{result}\n\n"
                    "此審核操作已記錄。"
                ),
                embed=None,
                view=None,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="❌ 退件",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def reject(
        self,
        interaction,
        button,
    ):
        try:
            actor = verify_user(
                interaction,
                self.actor,
            )

            get_review_character(
                actor,
                self.character_id,
            )

            await interaction.response.send_modal(
                RejectModal(
                    actor,
                    self.character_id,
                )
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="⬅️ 返回待審清單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        result = get_pending(
            self.actor
        )

        await interaction.response.edit_message(
            content=list_text(result),
            embed=None,
            view=ReviewListView(
                self.actor,
            ),
        )


class ReviewCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="審核",
        description="開啟官方統一審核中心",
    )
    @app_commands.guild_only()
    async def review(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = get_actor(interaction)

            result = await asyncio.to_thread(
                get_pending,
                actor,
            )

            await interaction.response.send_message(
                list_text(result),
                view=ReviewListView(actor),
                ephemeral=True,
            )

        except (
            InvalidState,
            PermissionDenied,
            ValueError,
        ) as exc:
            await send_error(interaction, exc)

        except sqlite3.Error:
            LOG.exception("審核中心資料庫錯誤")
            await send_error(
                interaction,
                "無法讀取審核資料，請聯絡管理員。",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        ReviewCenter(bot)
    )
