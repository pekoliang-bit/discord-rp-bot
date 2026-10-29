"""Discord RP 統一審核中心。

正式指令：/審核

整合：
- 角色雙階段審核
- 個人店鋪申請
- 銀行貸款申請
- 一般信用卡申請
- 黑卡申請

沿用現有業務核心及資料庫。
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
from modules.official_center import has_staff_permission
from modules.profile import FIELDS, missing_fields
from modules.player_shops import approve_store
from modules.loans import review_application as review_loan
from modules.credit_cards import review_card
from modules.black_card import review_black_card

from modules.event_project_manager import is_event_admin

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25

KINDS = {
    "character": (
        "🎭 角色雙審",
        "characters",
        "name",
        "status IN ('review_1','review_2')",
    ),
    "shop": (
        "🏪 個人店鋪",
        "personal_shop_applications",
        "name",
        "status = 'pending'",
    ),
    "loan": (
        "🏦 銀行貸款",
        "bank_loans",
        "purpose",
        "status = 'pending'",
    ),
    "credit": (
        "💳 信用卡",
        "credit_applications",
        "requested_limit",
        "status = 'pending'",
    ),
    "black": (
        "🖤 黑卡",
        "black_card_applications",
        "character_id",
        "status = 'pending'",
    ),
}


def authorized(
    interaction: discord.Interaction,
    owner: Actor | None = None,
) -> Actor:
    actor = Actor.from_interaction(interaction)

    if owner and (
        owner.guild_id != actor.guild_id
        or owner.user_id != actor.user_id
    ):
        raise PermissionDenied(
            "這不是你的審核介面。"
        )

    if not has_staff_permission(
        interaction,
        "reviewer",
    ):
        raise PermissionDenied(
            "只有正式審核員或伺服器擁有者能操作。"
        )

    return actor


def pending(actor: Actor, kind: str, page: int = 0):
    if kind not in KINDS:
        raise ValueError("不支援的申請種類。")

    _, table, display_col, condition = KINDS[kind]

    with get_connection() as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM {table}
            WHERE guild_id = ?
              AND {condition}
            """,
            (actor.guild_id,),
        ).fetchone()["n"]

        last = max(
            0,
            (total - 1) // PAGE_SIZE,
        )

        page = min(
            max(0, int(page)),
            last,
        )

        rows = conn.execute(
            f"""
            SELECT id,
                   {display_col} AS display,
                   status
            FROM {table}
            WHERE guild_id = ?
              AND {condition}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (
                actor.guild_id,
                PAGE_SIZE,
                PAGE_SIZE * page,
            ),
        ).fetchall()

    return (
        [dict(row) for row in rows],
        total,
        page,
        last,
    )


def record(actor: Actor, kind: str, number: int):
    if kind not in KINDS:
        raise ValueError("不支援的申請種類。")

    _, table, _, condition = KINDS[kind]

    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE guild_id = ?
              AND id = ?
              AND {condition}
            """,
            (
                actor.guild_id,
                number,
            ),
        ).fetchone()

        if not row:
            raise InvalidState(
                "申請已處理或不存在，請重新整理。"
            )

        item = dict(row)

        character_id = (
            number
            if kind == "character"
            else item["character_id"]
        )

        character = conn.execute(
            """
            SELECT id, name, owner_id,
                   status, age, occupation,
                   settings_json
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
                "申請對應角色不存在。"
            )

        return item, dict(character)


def approve_character(
    actor: Actor,
    number: int,
    approve: bool,
    reason: str,
):
    """角色雙階段審核。"""

    if not approve and not reason.strip():
        raise ValueError(
            "退件必須填寫理由。"
        )

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT owner_id, status
            FROM characters
            WHERE guild_id = ?
              AND id = ?
              AND character_type = 'player'
            """,
            (
                actor.guild_id,
                number,
            ),
        ).fetchone()

        if (
            not row
            or row["status"]
            not in ("review_1", "review_2")
        ):
            raise InvalidState(
                "角色已不是待審狀態。"
            )

        if str(row["owner_id"]) == actor.user_id:
            raise PermissionDenied(
                "不能審核自己的角色。"
            )

        progress = conn.execute(
            """
            SELECT first_reviewer
            FROM character_review_progress
            WHERE guild_id = ?
              AND character_id = ?
            """,
            (
                actor.guild_id,
                number,
            ),
        ).fetchone()

        if not progress:
            raise InvalidState(
                "缺少審核進度紀錄。"
            )

        old_status = row["status"]

        if not approve:
            new_status = "draft"

        elif old_status == "review_1":
            new_status = "review_2"

        else:
            if not progress["first_reviewer"]:
                raise InvalidState(
                    "缺少第一審紀錄。"
                )

            if (
                str(progress["first_reviewer"])
                == actor.user_id
            ):
                raise PermissionDenied(
                    "兩階段審核必須由不同人執行。"
                )

            new_status = "approved"

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
                new_status,
                actor.guild_id,
                number,
                old_status,
            ),
        )

        if changed.rowcount != 1:
            raise InvalidState(
                "角色審核狀態已變更。"
            )

        if approve and old_status == "review_1":
            conn.execute(
                """
                UPDATE character_review_progress
                SET first_reviewer = ?,
                    first_reviewed_at =
                        CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    number,
                ),
            )

        elif approve:
            conn.execute(
                """
                UPDATE character_review_progress
                SET second_reviewer = ?,
                    second_reviewed_at =
                        CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND character_id = ?
                """,
                (
                    actor.user_id,
                    actor.guild_id,
                    number,
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
                f"character:{number}",
                json.dumps(
                    {
                        "old": old_status,
                        "new": new_status,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    return {
        "draft": "退回草稿",
        "review_2": "第一審通過，待第二審",
        "approved": "雙審通過，待啟用",
    }[new_status]


def run_business_review(
    actor: Actor,
    kind: str,
    number: int,
    approve: bool,
    reason: str,
    amount: int | None = None,
    annual_bps: int | None = None,
):
    """呼叫指定的既有審核核心。

    注意：
    非管理員審核員的授權已由互動入口驗證。
    既有金融核心仍使用 Actor.is_admin，
    因此僅在呼叫指定的業務審核函式時
    建立授權代理。

    不得把此代理傳給其他管理功能。
    """

    if kind == "character":
        return approve_character(
            actor,
            number,
            approve,
            reason,
        )

    delegated = Actor(
        actor.guild_id,
        actor.user_id,
        True,
    )

    if kind == "shop":
        return approve_store(
            delegated,
            number,
            approve,
            reason,
        )

    if kind == "loan":
        return review_loan(
            delegated,
            number,
            approve,
            amount or 0,
            annual_bps or 0,
            reason,
        )

    if kind == "credit":
        return review_card(
            delegated,
            number,
            approve,
            amount or 0,
            reason,
        )

    if kind == "black":
        return review_black_card(
            delegated,
            number,
            approve,
            reason,
        )

    raise ValueError(
        "未知的審核業務。"
    )


def description(
    actor: Actor,
    kind: str,
    number: int,
):
    application, character = record(
        actor,
        kind,
        number,
    )

    name = discord.utils.escape_markdown(
        str(character["name"])
    )[:100]

    embed = discord.Embed(
        title=(
            f"{KINDS[kind][0]}"
            f"｜申請 #{number}"
        ),
        description=(
            f"申請角色：**{name}**\n"
            f"擁有者：<@{character['owner_id']}>\n"
            f"狀態：{application['status']}"
        ),
        color=discord.Color.blurple(),
    )

    if kind == "character":
        settings = json.loads(
            character["settings_json"] or "{}"
        )

        embed.add_field(
            name="基本資料",
            value=(
                f"年齡：{character['age']}\n"
                f"職業：{character['occupation']}"
            ),
            inline=False,
        )

        content = "\n".join(
            f"{label}："
            f"{str(settings.get(key) or '未填')[:130]}"
            for label, key in FIELDS.items()
            if key not in (
                "name",
                "age",
                "occupation",
            )
        )

        for index in range(
            0,
            min(len(content), 5000),
            950,
        ):
            embed.add_field(
                name=(
                    "角色資料"
                    if index == 0
                    else "角色資料（續）"
                ),
                value=content[
                    index:index + 950
                ] or "無",
                inline=False,
            )

        missing = missing_fields(character)

        if missing:
            embed.add_field(
                name="尚缺必填",
                value="、".join(missing)[:1000],
                inline=False,
            )

    else:
        fields = {
            "shop": (
                ("店鋪名稱", "name"),
                ("類別", "category"),
                ("等級", "level"),
                ("簡介", "description"),
            ),
            "loan": (
                ("類型", "loan_type"),
                ("申請金額", "requested_amount"),
                ("用途", "purpose"),
            ),
            "credit": (
                ("申請額度", "requested_limit"),
            ),
            "black": (
                ("角色年齡", "age"),
            ),
        }[kind]

        for label, key in fields:
            value = (
                character.get(key)
                if kind == "black"
                else application.get(key)
            )

            embed.add_field(
                name=label,
                value=discord.utils.escape_mentions(
                    str(value or "未填")
                )[:950],
                inline=False,
            )

    return embed


async def send_error(interaction, exc):
    if isinstance(exc, sqlite3.Error):
        LOG.exception(
            "審核資料庫發生錯誤"
        )
        message = (
            "資料庫操作失敗，請聯絡管理員。"
        )
    else:
        message = str(exc)[:1600]

    if interaction.response.is_done():
        await interaction.followup.send(
            f"⚠️ {message}",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"⚠️ {message}",
            ephemeral=True,
        )


class Guarded(discord.ui.View):
    def __init__(self, actor):
        super().__init__(timeout=600)
        self.actor = actor

    async def interaction_check(
        self,
        interaction,
    ):
        try:
            authorized(
                interaction,
                self.actor,
            )
            return True

        except (
            PermissionDenied,
            InvalidState,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )
            return False


class KindSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="選擇審核種類",
            options=[
                discord.SelectOption(
                    label=label,
                    value=kind,
                )
                for kind, (
                    label,
                    _,
                    _,
                    _,
                ) in KINDS.items()
            ],
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        kind = self.values[0]

        _, total, page, last = pending(
            self.view.actor,
            kind,
        )

        await interaction.response.edit_message(
            content=(
                f"{KINDS[kind][0]}\n"
                f"待審：{total}件\n"
                f"第 {page + 1}/{last + 1} 頁"
            ),
            embed=None,
            view=PendingView(
                self.view.actor,
                kind,
            ),
        )


class Home(Guarded):
    def __init__(self, actor):
        super().__init__(actor)
        self.add_item(KindSelect())


class ItemSelect(discord.ui.Select):
    def __init__(self, rows):
        options = [
            discord.SelectOption(
                label=(
                    f"#{row['id']} "
                    f"{str(row['display'])[:75]}"
                ),
                description=str(
                    row["status"]
                )[:100],
                value=str(row["id"]),
            )
            for row in rows
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有待審案件",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇申請案件",
            options=options,
            disabled=not bool(rows),
        )

    async def callback(self, interaction):
        if not await self.view.interaction_check(
            interaction
        ):
            return

        kind = self.view.kind
        number = int(self.values[0])

        try:
            embed = description(
                self.view.actor,
                kind,
                number,
            )

            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=DetailView(
                    self.view.actor,
                    kind,
                    number,
                ),
            )

        except (
            InvalidState,
            ValueError,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )


class PendingView(Guarded):
    def __init__(
        self,
        actor,
        kind,
        page=0,
    ):
        super().__init__(actor)

        self.kind = kind

        rows, self.total, self.page, self.last = (
            pending(actor, kind, page)
        )

        self.add_item(ItemSelect(rows))

        self.previous.disabled = self.page <= 0
        self.next.disabled = (
            self.page >= self.last
        )

    async def move(self, interaction, page):
        view = PendingView(
            self.actor,
            self.kind,
            page,
        )

        await interaction.response.edit_message(
            content=(
                f"{KINDS[self.kind][0]}\n"
                f"待審：{view.total}件\n"
                f"第 {view.page + 1}/"
                f"{view.last + 1} 頁"
            ),
            embed=None,
            view=view,
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
        await self.move(
            interaction,
            self.page - 1,
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
        await self.move(
            interaction,
            self.page + 1,
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
        await self.move(
            interaction,
            self.page,
        )

    @discord.ui.button(
        label="📂 返回分類",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def home(
        self,
        interaction,
        button,
    ):
        await interaction.response.edit_message(
            content=(
                "🛡️ **統一審核中心**\n"
                "請選擇案件分類。"
            ),
            embed=None,
            view=Home(self.actor),
        )


class DecisionModal(discord.ui.Modal):
    def __init__(
        self,
        actor,
        kind,
        number,
        approve,
    ):
        super().__init__(
            title=(
                "核准申請"
                if approve
                else "退件／拒絕申請"
            )
        )

        self.actor = actor
        self.kind = kind
        self.number = number
        self.approve = approve

        self.reason = discord.ui.TextInput(
            label="審核意見／退件原因",
            required=not approve,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.reason)

        self.amount = None
        self.rate = None

        if approve and kind in ("loan", "credit"):
            application, _ = record(
                actor,
                kind,
                number,
            )

            request_amount = application[
                (
                    "requested_amount"
                    if kind == "loan"
                    else "requested_limit"
                )
            ]

            self.amount = discord.ui.TextInput(
                label="核准金額／信用額度",
                default=str(request_amount),
                max_length=15,
            )

            self.add_item(self.amount)

            if kind == "loan":
                self.rate = discord.ui.TextInput(
                    label="貸款年利率（基點，100=1%）",
                    default="500",
                    max_length=5,
                )

                self.add_item(self.rate)

    async def on_submit(self, interaction):
        try:
            actor = authorized(
                interaction,
                self.actor,
            )

            amount = (
                int(self.amount.value)
                if self.amount
                else None
            )

            rate = (
                int(self.rate.value)
                if self.rate
                else None
            )

            reason = str(
                self.reason.value or ""
            ).strip()

            # 再次驗證案件仍待審。
            record(
                actor,
                self.kind,
                self.number,
            )

            # 執行前再次確認官方授權。
            authorized(
                interaction,
                self.actor,
            )

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            result = await asyncio.to_thread(
                run_business_review,
                actor,
                self.kind,
                self.number,
                self.approve,
                reason,
                amount,
                rate,
            )

            await interaction.followup.send(
                (
                    f"✅ 案件 #{self.number}\n"
                    f"結果：{result}"
                ),
                ephemeral=True,
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )


class DetailView(Guarded):
    def __init__(
        self,
        actor,
        kind,
        number,
    ):
        super().__init__(actor)

        self.kind = kind
        self.number = number

    @discord.ui.button(
        label="✅ 核准／通過本階段",
        style=discord.ButtonStyle.success,
    )
    async def approve(
        self,
        interaction,
        button,
    ):
        try:
            record(
                self.actor,
                self.kind,
                self.number,
            )

            await interaction.response.send_modal(
                DecisionModal(
                    self.actor,
                    self.kind,
                    self.number,
                    True,
                )
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="❌ 退件／拒絕",
        style=discord.ButtonStyle.danger,
    )
    async def reject(
        self,
        interaction,
        button,
    ):
        try:
            record(
                self.actor,
                self.kind,
                self.number,
            )

            await interaction.response.send_modal(
                DecisionModal(
                    self.actor,
                    self.kind,
                    self.number,
                    False,
                )
            )

        except (
            ValueError,
            InvalidState,
            PermissionDenied,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )

    @discord.ui.button(
        label="⬅️ 返回待審",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction,
        button,
    ):
        view = PendingView(
            self.actor,
            self.kind,
        )

        await interaction.response.edit_message(
            content=(
                f"{KINDS[self.kind][0]}\n"
                f"待審：{view.total}件"
            ),
            embed=None,
            view=view,
        )




class EventReviewNumberModal(discord.ui.Modal, title="活動企劃審核"):
    event_id = discord.ui.TextInput(label="企劃編號", max_length=12)
    result = discord.ui.TextInput(label="結果：通過 或 駁回", max_length=2)
    note = discord.ui.TextInput(label="審核意見", style=discord.TextStyle.paragraph, required=False, max_length=500)

    async def on_submit(self, interaction):
        from modules.event_project_manager import review, is_owner, sync_role
        try:
            if not is_event_admin(interaction):
                raise PermissionError("需要正式活動管理權限。")
            value = str(self.result).strip()
            if value not in ("通過", "駁回"):
                raise ValueError("結果只能填通過或駁回。")
            eid = int(str(self.event_id).strip())
            event = review(interaction.guild_id, interaction.user.id, eid,
                           value == "通過", str(self.note), is_owner(interaction))
            if value == "通過":
                await sync_role(interaction, event["organizer_id"], True)
            await interaction.response.send_message(f"✅ 活動企劃 #{eid} 已{value}。", ephemeral=True)
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class EventReviewButtons(discord.ui.View):
    def __init__(self, uid, include_legacy=False):
        super().__init__(timeout=600)
        self.uid = uid

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid or not is_event_admin(interaction):
            await interaction.response.send_message("沒有活動審核權限。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="活動企劃待審", style=discord.ButtonStyle.primary)
    async def project_list(self, interaction, button):
        with get_connection() as db:
            rows = db.execute(
                "SELECT id, title, creator_id FROM rp_event_projects WHERE guild_id=? AND status='submitted' ORDER BY id LIMIT 20",
                (str(interaction.guild_id),)
            ).fetchall()
        message = "\n".join(
            f"#{r['id']}｜{discord.utils.escape_mentions(r['title'][:50])}｜<@{r['creator_id']}>"
            for r in rows
        ) or "目前沒有待審活動企劃。"
        await interaction.response.send_message(message[:1900], ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="審核活動企劃", style=discord.ButtonStyle.success)
    async def project_review(self, interaction, button):
        await interaction.response.send_modal(EventReviewNumberModal())

    @discord.ui.button(label="活動任務證明", style=discord.ButtonStyle.primary)
    async def task_review(self, interaction, button):
        await interaction.response.send_modal(EventTaskIdModal())


class EventTaskIdModal(discord.ui.Modal, title="查看活動任務待審"):
    event_id = discord.ui.TextInput(label="活動編號", max_length=12)

    async def on_submit(self, interaction):
        from modules.event_task_review_ui import ReviewPanel, eligible
        from modules.event_project_manager import fetch_event
        try:
            eid = int(str(self.event_id).strip())
            with get_connection() as db:
                event = dict(fetch_event(db, interaction.guild_id, eid))
            if not is_event_admin(interaction) or not eligible(interaction, event):
                raise PermissionError("沒有審核此活動的權限。")
            view = ReviewPanel(interaction, eid)
            await interaction.response.send_message(
                embed=view.make_embed(), view=view, ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)


class EventReviewOpen(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🎪 活動審核", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction):
        if not is_event_admin(interaction):
            await interaction.response.send_message("沒有活動審核權限。", ephemeral=True)
            return
        await interaction.response.send_message(
            "🎪 **活動案件審核**", view=EventReviewButtons(interaction.user.id), ephemeral=True
        )


def make_review_home(actor, interaction):
    view = Home(actor)
    from modules.application_extras import OpenGeneralReview
    from modules.company_business_review_ui import OpenBusinessReview
    view.add_item(OpenGeneralReview())
    view.add_item(OpenBusinessReview())
    if is_event_admin(interaction):
        view.add_item(EventReviewOpen())
    return view


class ReviewCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="審核",
        description="角色、店鋪與金融申請統一審核",
    )
    @app_commands.guild_only()
    async def review(
        self,
        interaction: discord.Interaction,
    ):
        try:
            # 活動管理員不必同時擁有角色/金融審核職務。
            if is_event_admin(interaction) and not has_staff_permission(interaction, "reviewer"):
                return await interaction.response.send_message(
                    "🎪 **活動審核中心**", view=EventReviewButtons(interaction.user.id), ephemeral=True,
                )
            actor = authorized(interaction)

            await interaction.response.send_message(
                (
                    "🛡️ **統一審核中心**\n\n"
                    "請選擇要處理的申請類型：\n"
                    "角色、店鋪、貸款、"
                    "信用卡、黑卡、公司、品牌或集團。"
                ),
                view=make_review_home(actor, interaction),
                ephemeral=True,
            )

        except (
            PermissionDenied,
            InvalidState,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(
                interaction,
                exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        ReviewCenter(bot)
    )
