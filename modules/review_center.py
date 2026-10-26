
"""Discord RP：統一審核中心——角色雙階段審核。

正式指令：/審核

權限：
- 伺服器擁有者
- 經 /官方 核准的審核員

功能：
- 待審角色分頁
- 查看完整送審資料
- 第一階段審核
- 第二階段審核
- 退件與理由
- 防止自行審核
- 防止同一人完成兩審
- 狀態更新及稽核紀錄
- 操作時重新驗證身分及角色狀態

依賴：
modules.official_center
core.database
core.permissions
modules.profile
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction
from core.permissions import (
    Actor,
    InvalidState,
    PermissionDenied,
)
from modules.official_center import (
    has_staff_permission,
)
from modules.profile import (
    FIELDS,
    missing_fields,
)

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25
TIMEOUT = 600

STAGES = {
    "review_1": "第一階段",
    "review_2": "第二階段",
}


def ensure_reviewer(interaction):
    """驗證操作者具有正式審核權限。"""

    actor = Actor.from_interaction(interaction)

    if not has_staff_permission(
        interaction,
        "reviewer",
    ):
        raise PermissionDenied(
            "只有正式審核員或伺服器擁有者"
            "可以使用此功能。"
        )

    return actor


def check_interaction(interaction, original):
    actor = ensure_reviewer(interaction)

    if (
        actor.guild_id != original.guild_id
        or actor.user_id != original.user_id
    ):
        raise PermissionDenied(
            "這不是你的審核介面。"
        )

    return actor


def ensure_review_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS
        character_review_progress (
            guild_id TEXT NOT NULL,
            character_id INTEGER NOT NULL,
            round_number INTEGER NOT NULL DEFAULT 0,
            first_reviewer TEXT,
            first_reviewed_at TEXT,
            second_reviewer TEXT,
            second_reviewed_at TEXT,
            PRIMARY KEY (guild_id, character_id)
        )
        """
    )


def get_pending(actor, page=0):
    page = max(0, int(page))

    with get_connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM characters
            WHERE guild_id = ?
              AND character_type = 'player'
              AND status IN ('review_1','review_2')
            """,
            (actor.guild_id,),
        ).fetchone()["n"]

        max_page = max(
            0,
            (total - 1) // PAGE_SIZE,
        )

        page = min(page, max_page)

        rows = conn.execute(
            """
            SELECT id, name, occupation,
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
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "max_page": max_page,
    }


def get_character(actor, character_id):
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
                character_id,
            ),
        ).fetchone()

    if row is None:
        raise InvalidState("角色不存在。")

    if row["status"] not in STAGES:
        raise InvalidState(
            "角色已不在待審狀態，請重新整理。"
        )

    return dict(row)


def get_progress(actor, character_id):
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
                    character_id,
                ),
            ).fetchone()

        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise

    return dict(row) if row else None


def review_character_as_staff(
    actor,
    character_id,
    approve,
    reason="",
    *,
    authorized=False,
):
    """由已驗證授權的審核員進行原子化雙審。

    僅限本模組的互動處理器呼叫。
    不能直接對玩家開放此函式。
    """

    if not authorized:
        raise PermissionDenied(
            "缺少審核授權驗證。"
        )

    reason = (reason or "").strip()

    if len(reason) > 500:
        raise ValueError(
            "審核理由最多500字。"
        )

    if not approve and not reason:
        raise ValueError(
            "退件必須填寫理由。"
        )

    with transaction() as conn:
        ensure_review_table(conn)

        character = conn.execute(
            """
            SELECT id, name, owner_id,
                   character_type, status
            FROM characters
            WHERE guild_id = ?
              AND id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if character is None:
            raise InvalidState(
                "找不到角色。"
            )

        if character["character_type"] != "player":
            raise InvalidState(
                "此流程只審核玩家原創角色。"
            )

        if str(character["owner_id"]) == actor.user_id:
            raise PermissionDenied(
                "不能審核自己的角色。"
            )

        old = character["status"]

        if old not in STAGES:
            raise InvalidState(
                "角色不在待審狀態。"
            )

        progress = conn.execute(
            """
            SELECT *
            FROM character_review_progress
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                character_id,
            ),
        ).fetchone()

        if progress is None:
            raise InvalidState(
                "缺少送審紀錄，請檢查角色送審流程。"
            )

        if not approve:
            new = "draft"

        elif old == "review_1":
            new = "review_2"

        else:
            first = progress["first_reviewer"]

            if not first:
                raise InvalidState(
                    "缺少第一審通過紀錄。"
                )

            if str(first) == actor.user_id:
                raise PermissionDenied(
                    "第一審與第二審必須由不同人執行。"
                )

            new = "approved"

        changed = conn.execute(
            """
            UPDATE characters
            SET status = ?,
                version = version + 1
            WHERE guild_id = ?
              AND id = ?
              AND status = ?
            """,
            (
                new,
                actor.guild_id,
                character_id,
                old,
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "角色審核狀態已變更。"
            )

        if approve and old == "review_1":
            conn.execute(
                """
                UPDATE character_review_progress
                SET first_reviewer = ?,
                    first_reviewed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        elif approve:
            conn.execute(
                """
                UPDATE character_review_progress
                SET second_reviewer = ?,
                    second_reviewed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    character_id,
                ),
            )

        conn.execute(
            """
            INSERT INTO audit_events (
                guild_id,
                actor_id,
                action,
                target_key,
                details_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                actor.guild_id,
                actor.user_id,
                "character.review",
                f"character:{character_id}",
                json.dumps(
                    {
                        "from": old,
                        "to": new,
                        "approve": approve,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    return {
        "draft": "退回草稿",
        "review_2": "第一審通過，等待第二審",
        "approved": "雙審通過，等待玩家確認啟用",
    }[new]


def format_list(result):
    return (
        "🛡️ **官方審核中心｜角色審核**\n\n"
        f"待審角色：{result['total']}名\n"
        f"第{result['page'] + 1}/"
        f"{result['max_page'] + 1}頁\n\n"
        "請從下方選擇角色。\n"
        "每次審核都會檢查正式授權。"
    )


def display(value, limit=300):
    if value is None or value == "":
        return "未填寫"

    if isinstance(value, (dict, list)):
        value = json.dumps(
            value,
            ensure_ascii=False,
        )

    return str(value)[:limit]


def review_embed(actor, character_id):
    character = get_character(
        actor,
        character_id,
    )

    try:
        settings = json.loads(
            character["settings_json"] or "{}"
        )
    except (ValueError, TypeError):
        settings = {}

    progress = get_progress(
        actor,
        character_id,
    )

    embed = discord.Embed(
        title=f"角色審核｜#{character_id}",
        description=(
            f"**{display(character['name'], 100)}**\n"
            f"審核階段：{STAGES[character['status']]}\n"
            f"申請人：<@{character['owner_id']}>"
        ),
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="基本資料",
        value=(
            f"年齡：{character['age']}\n"
            f"職業：{display(character['occupation'])}"
        ),
        inline=False,
    )

    lines = []

    for label, key in FIELDS.items():
        if key in ("name", "age", "occupation"):
            continue

        lines.append(
            f"**{label}**："
            f"{display(settings.get(key), 240)}"
        )

    for label, key in (
        ("生日", "birthday"),
        ("感情狀態", "relationship_status"),
        ("伴侶", "partner_name"),
    ):
        lines.append(
            f"**{label}**："
            f"{display(settings.get(key), 200)}"
        )

    chunks = []
    chunk = ""

    for line in lines:
        if len(chunk) + len(line) + 1 > 1000:
            chunks.append(chunk)
            chunk = ""

        chunk += line + "\n"

    if chunk:
        chunks.append(chunk)

    for index, content in enumerate(chunks[:15]):
        embed.add_field(
            name=(
                "角色設定"
                if index == 0
                else f"角色設定（{index + 1}）"
            ),
            value=content,
            inline=False,
        )

    if progress:
        first = progress.get("first_reviewer")

        embed.add_field(
            name="審核紀錄",
            value=(
                "第一審："
                + (
                    f"<@{first}>"
                    if first
                    else "未完成"
                )
            ),
            inline=False,
        )

    missing = missing_fields(character)

    if missing:
        embed.add_field(
            name="⚠️ 必填資料",
            value="、".join(missing)[:1000],
            inline=False,
        )

    return embed


async def respond_error(interaction, exc):
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
        super().__init__(timeout=TIMEOUT)
        self.actor = actor

    async def interaction_check(self, interaction):
        try:
            check_interaction(
                interaction,
                self.actor,
            )
            return True

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await respond_error(interaction, exc)
            return False


class PendingSelect(discord.ui.Select):
    def __init__(self, actor, result):
        self.actor = actor

        items = result["items"]

        options = [
            discord.SelectOption(
                label=(
                    f"#{row['id']} {row['name']}"
                )[:100],
                description=(
                    f"{STAGES[row['status']]}｜"
                    f"{row['occupation'] or '未設定職業'}"
                )[:100],
                value=str(row["id"]),
            )
            for row in items
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有待審角色",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇待審角色",
            options=options,
            disabled=not bool(items),
            row=0,
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        try:
            cid = int(self.values[0])

            await interaction.response.edit_message(
                content=None,
                embed=review_embed(self.actor, cid),
                view=DetailView(self.actor, cid),
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await respond_error(interaction, exc)


class PendingView(AuthorizedView):
    def __init__(self, actor, page=0):
        super().__init__(actor)

        result = get_pending(actor, page)

        self.page = result["page"]
        self.max_page = result["max_page"]

        self.add_item(
            PendingSelect(actor, result)
        )

        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.max_page

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):
        result = get_pending(
            self.actor,
            self.page - 1,
        )

        await interaction.response.edit_message(
            content=format_list(result),
            embed=None,
            view=PendingView(
                self.actor,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="下一頁 ➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(self, interaction, button):
        result = get_pending(
            self.actor,
            self.page + 1,
        )

        await interaction.response.edit_message(
            content=format_list(result),
            embed=None,
            view=PendingView(
                self.actor,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="🔄 重新整理",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def refresh(self, interaction, button):
        result = get_pending(
            self.actor,
            self.page,
        )

        await interaction.response.edit_message(
            content=format_list(result),
            embed=None,
            view=PendingView(
                self.actor,
                result["page"],
            ),
        )


class RejectModal(discord.ui.Modal):
    def __init__(self, actor, character_id):
        super().__init__(title="角色審核｜退件理由")

        self.actor = actor
        self.character_id = character_id

        self.reason = discord.ui.TextInput(
            label="退件原因",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
        )

        self.add_item(self.reason)

    async def on_submit(self, interaction):
        try:
            actor = check_interaction(
                interaction,
                self.actor,
            )

            # 在資料庫寫入前檢查當前授權。
            result = await asyncio.to_thread(
                review_character_as_staff,
                actor,
                self.character_id,
                False,
                str(self.reason.value),
                authorized=True,
            )

            await interaction.response.send_message(
                (
                    f"✅ 角色 #{self.character_id}"
                    f" 已處理：{result}\n\n"
                    f"原因：{self.reason.value}"
                ),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await respond_error(interaction, exc)


class DetailView(AuthorizedView):
    def __init__(self, actor, character_id):
        super().__init__(actor)
        self.character_id = character_id

    @discord.ui.button(
        label="✅ 通過本階段",
        style=discord.ButtonStyle.success,
    )
    async def approve(self, interaction, button):
        try:
            actor = check_interaction(
                interaction,
                self.actor,
            )

            # 先確認最新狀態。
            get_character(
                actor,
                self.character_id,
            )

            await interaction.response.defer(
                ephemeral=True,
            )

            result = await asyncio.to_thread(
                review_character_as_staff,
                actor,
                self.character_id,
                True,
                authorized=True,
            )

            await interaction.edit_original_response(
                content=(
                    f"✅ 角色 #{self.character_id}\n"
                    f"{result}"
                ),
                embed=None,
                view=None,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await respond_error(interaction, exc)

    @discord.ui.button(
        label="❌ 退件",
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction, button):
        try:
            check_interaction(
                interaction,
                self.actor,
            )

            await interaction.response.send_modal(
                RejectModal(
                    self.actor,
                    self.character_id,
                )
            )

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await respond_error(interaction, exc)

    @discord.ui.button(
        label="⬅️ 返回清單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, interaction, button):
        result = get_pending(self.actor)

        await interaction.response.edit_message(
            content=format_list(result),
            embed=None,
            view=PendingView(self.actor),
        )


class ReviewCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="審核",
        description="官方角色雙階段審核中心",
    )
    @app_commands.guild_only()
    async def review(
        self,
        interaction: discord.Interaction,
    ):
        try:
            actor = ensure_reviewer(
                interaction
            )

            result = get_pending(actor)

            await interaction.response.send_message(
                format_list(result),
                view=PendingView(actor),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
        ) as exc:
            await respond_error(interaction, exc)

        except sqlite3.Error:
            LOG.exception("審核中心讀取失敗")

            await respond_error(
                interaction,
                "審核資料庫發生錯誤。",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        ReviewCenter(bot)
    )
