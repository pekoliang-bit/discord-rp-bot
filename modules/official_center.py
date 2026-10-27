"""Discord RP 官方人員管理中心。

正式指令：/官方

功能：
- 玩家申請審核員或紀律人員
- 查看自己的申請紀錄
- 伺服器擁有者設定專用身分組
- 人員申請完整分頁
- 查看申請理由
- 核准與駁回
- 正式人員完整分頁
- 撤銷官方職位
- Discord 身分組同步
- 角色與伺服器權限隔離
- 稽核事件紀錄

供其他模組使用：
has_staff_permission(interaction, staff_type)
require_staff_permission(interaction, staff_type)
"""

from __future__ import annotations

import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection, transaction

LOG = logging.getLogger(__name__)

PAGE_SIZE = 25
TIMEOUT = 600

STAFF_TYPES = {
    "reviewer": "審核員",
    "discipline": "紀律人員",
}


# ==================================================
# 資料庫
# ==================================================

def init_official_tables():
    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            official_role_config (
                guild_id TEXT NOT NULL,
                staff_type TEXT NOT NULL,
                role_id TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, staff_type),
                CHECK (
                    staff_type IN (
                        'reviewer', 'discipline'
                    )
                )
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            official_staff_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                staff_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer_id TEXT,
                review_reason TEXT,
                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT,
                CHECK (
                    staff_type IN (
                        'reviewer', 'discipline'
                    )
                ),
                CHECK (
                    status IN (
                        'pending', 'approved',
                        'rejected', 'cancelled'
                    )
                )
            )
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_official_staff_pending
            ON official_staff_applications (
                guild_id, user_id, staff_type
            )
            WHERE status = 'pending'
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
            official_staff_grants (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                staff_type TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                granted_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id, user_id, staff_type
                ),
                CHECK (
                    staff_type IN (
                        'reviewer', 'discipline'
                    )
                )
            )
            """
        )


def audit(conn, guild_id, actor_id, action,
          target, details):
    conn.execute(
        """
        INSERT INTO audit_events (
            guild_id, actor_id, action,
            target_key, details_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(guild_id),
            str(actor_id),
            action,
            str(target),
            json.dumps(
                details,
                ensure_ascii=False,
            ),
        ),
    )


# ==================================================
# 權限驗證
# ==================================================

def is_owner(interaction):
    return (
        interaction.guild is not None
        and interaction.guild.owner_id
        == interaction.user.id
    )


def require_owner(interaction):
    if not is_owner(interaction):
        raise PermissionError(
            "只有伺服器擁有者可以執行此操作。"
        )


def require_same_user(interaction, user_id, guild_id):
    if (
        str(interaction.user.id) != str(user_id)
        or str(interaction.guild_id) != str(guild_id)
    ):
        raise PermissionError(
            "這不是你的操作介面。"
        )


def has_staff_permission(interaction, staff_type):
    """授權須同時具備資料庫紀錄及 Discord 身分組。"""

    if staff_type not in STAFF_TYPES:
        return False

    if interaction.guild is None:
        return False

    if is_owner(interaction):
        return True

    member = interaction.user

    if not isinstance(member, discord.Member):
        return False

    guild_id = str(interaction.guild_id)
    user_id = str(member.id)

    with get_connection() as conn:
        grant = conn.execute(
            """
            SELECT 1
            FROM official_staff_grants
            WHERE guild_id = ?
              AND user_id = ?
              AND staff_type = ?
            """,
            (guild_id, user_id, staff_type),
        ).fetchone()

        config = conn.execute(
            """
            SELECT role_id
            FROM official_role_config
            WHERE guild_id = ?
              AND staff_type = ?
            """,
            (guild_id, staff_type),
        ).fetchone()

    if grant is None or config is None:
        return False

    role_id = int(config["role_id"])

    role = interaction.guild.get_role(role_id)

    if role is None:
        return False

    return any(
        member_role.id == role_id
        for member_role in member.roles
    )


def require_staff_permission(interaction, staff_type):
    if not has_staff_permission(
        interaction,
        staff_type,
    ):
        raise PermissionError(
            "你沒有這項官方職位的正式授權。"
        )


def get_config(guild_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT staff_type, role_id
            FROM official_role_config
            WHERE guild_id = ?
            """,
            (str(guild_id),),
        ).fetchall()

    return {
        row["staff_type"]: str(row["role_id"])
        for row in rows
    }


def validate_role(interaction, role):
    require_owner(interaction)

    if role.is_default() or role.managed:
        raise ValueError(
            "不能使用 @everyone 或整合服務身分組。"
        )

    if (
        role.permissions.administrator
        or role.permissions.manage_guild
        or role.permissions.manage_roles
    ):
        raise ValueError(
            "官方職位身分組不可具有伺服器管理權限。"
        )

    me = interaction.guild.me

    if (
        me is None
        or not me.guild_permissions.manage_roles
        or role >= me.top_role
    ):
        raise PermissionError(
            "機器人無權管理此身分組。"
            "請先調整 Discord 身分組階級。"
        )


# ==================================================
# 申請資料與分頁
# ==================================================

def paginate(total, page):
    last = max(0, (total - 1) // PAGE_SIZE)
    page = max(0, min(int(page), last))

    return page, last


def get_pending(guild_id, page=0):
    with get_connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM official_staff_applications
            WHERE guild_id = ?
              AND status = 'pending'
            """,
            (str(guild_id),),
        ).fetchone()["total"]

        page, last = paginate(total, page)

        rows = conn.execute(
            """
            SELECT *
            FROM official_staff_applications
            WHERE guild_id = ?
              AND status = 'pending'
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (
                str(guild_id),
                PAGE_SIZE,
                page * PAGE_SIZE,
            ),
        ).fetchall()

    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "last": last,
    }


def get_grants(guild_id, page=0):
    with get_connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM official_staff_grants
            WHERE guild_id = ?
            """,
            (str(guild_id),),
        ).fetchone()["total"]

        page, last = paginate(total, page)

        rows = conn.execute(
            """
            SELECT *
            FROM official_staff_grants
            WHERE guild_id = ?
            ORDER BY staff_type, granted_at, user_id
            LIMIT ? OFFSET ?
            """,
            (
                str(guild_id),
                PAGE_SIZE,
                page * PAGE_SIZE,
            ),
        ).fetchall()

    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "last": last,
    }


def get_my_applications(guild_id, user_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, staff_type, status,
                   created_at, reviewed_at,
                   review_reason
            FROM official_staff_applications
            WHERE guild_id = ?
              AND user_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (str(guild_id), str(user_id)),
        ).fetchall()

    return [dict(row) for row in rows]


def apply_staff(guild_id, user_id, kind, reason):
    if kind not in STAFF_TYPES:
        raise ValueError("職位不存在。")


    reason = reason.strip()

    if not 2 <= len(reason) <= 32:
        raise ValueError(
            "身分專用暱稱必須介於2至32字。"
        )


    with transaction() as conn:
        existing = conn.execute(
            """
            SELECT 1
            FROM official_staff_grants
            WHERE guild_id = ?
              AND user_id = ?
              AND staff_type = ?
            """,
            (
                str(guild_id),
                str(user_id),
                kind,
            ),
        ).fetchone()

        if existing:
            raise ValueError(
                "你已獲得這項職位，不能重複申請。"
            )

        try:
            result = conn.execute(
                """
                INSERT INTO official_staff_applications (
                    guild_id, user_id,
                    staff_type, reason
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    str(user_id),
                    kind,
                    reason,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "此職位已有待審申請。"
            ) from exc

        application_id = result.lastrowid

        audit(
            conn,
            guild_id,
            user_id,
            "official.staff.apply",
            f"staff_application:{application_id}",
            {"staff_type": kind},
        )

        return application_id


def get_application(guild_id, application_id):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM official_staff_applications
            WHERE guild_id = ?
              AND id = ?
              AND status = 'pending'
            """,
            (
                str(guild_id),
                int(application_id),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "申請不存在或已處理。"
        )

    return dict(row)


# ==================================================
# 身分組綁定、核准、撤銷
# ==================================================

def set_binding(interaction, kind, role):
    require_owner(interaction)
    validate_role(interaction, role)

    if kind not in STAFF_TYPES:
        raise ValueError("職位不正確。")

    guild_id = str(interaction.guild_id)

    with transaction() as conn:
        existing_grants = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM official_staff_grants
            WHERE guild_id = ?
              AND staff_type = ?
            """,
            (guild_id, kind),
        ).fetchone()["total"]

        old = conn.execute(
            """
            SELECT role_id
            FROM official_role_config
            WHERE guild_id = ?
              AND staff_type = ?
            """,
            (guild_id, kind),
        ).fetchone()

        if (
            existing_grants
            and old is not None
            and str(old["role_id"]) != str(role.id)
        ):
            raise ValueError(
                "這個職位已有正式人員。"
                "請先撤銷相關授權，"
                "再變更綁定身分組。"
            )

        other = conn.execute(
            """
            SELECT staff_type
            FROM official_role_config
            WHERE guild_id = ?
              AND role_id = ?
              AND staff_type != ?
            """,
            (
                guild_id,
                str(role.id),
                kind,
            ),
        ).fetchone()

        if other:
            raise ValueError(
                "這個身分組已綁定另一個官方職位。"
                "兩個職位必須使用不同身分組。"
            )

        conn.execute(
            """
            INSERT INTO official_role_config (
                guild_id, staff_type,
                role_id, updated_by
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, staff_type)
            DO UPDATE SET
                role_id = excluded.role_id,
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                guild_id,
                kind,
                str(role.id),
                str(interaction.user.id),
            ),
        )

        audit(
            conn,
            guild_id,
            interaction.user.id,
            "official.staff.role_bind",
            f"staff_type:{kind}",
            {"role_id": str(role.id)},
        )


async def fetch_member(guild, user_id):
    member = guild.get_member(int(user_id))

    if member is not None:
        return member

    try:
        return await guild.fetch_member(int(user_id))
    except discord.NotFound as exc:
        raise ValueError(
            "該使用者目前不在伺服器內。"
        ) from exc


async def decide_application(
    interaction,
    application_id,
    approve,
    reason="",
):
    require_owner(interaction)

    guild = interaction.guild
    guild_id = str(guild.id)

    application = get_application(
        guild_id,
        application_id,
    )

    kind = application["staff_type"]
    member = None
    role = None
    added_role = False

    if approve:
        config = get_config(guild_id)
        role_id = config.get(kind)

        if not role_id:
            raise ValueError(
                "請先設定對應的官方身分組。"
            )

        role = guild.get_role(int(role_id))

        if role is None:
            raise ValueError(
                "官方身分組已不存在。"
            )

        validate_role(interaction, role)

        member = await fetch_member(
            guild,
            application["user_id"],
        )

        if role not in member.roles:
            await member.add_roles(
                role,
                reason=(
                    f"官方人員申請 #{application_id} 核准"
                ),
            )
            added_role = True

    try:
        with transaction() as conn:
            changed = conn.execute(
                """
                UPDATE official_staff_applications
                SET status = ?,
                    reviewer_id = ?,
                    review_reason = ?,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ?
                  AND id = ?
                  AND status = 'pending'
                """,
                (
                    "approved" if approve else "rejected",
                    str(interaction.user.id),
                    reason[:500],
                    guild_id,
                    int(application_id),
                ),
            )

            if changed.rowcount != 1:
                raise ValueError(
                    "申請已由其他操作處理。"
                )

            if approve:
                conn.execute(
                    """
                    INSERT INTO official_staff_grants (
                        guild_id, user_id,
                        staff_type, granted_by
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(
                        guild_id, user_id, staff_type
                    )
                    DO UPDATE SET
                        granted_by = excluded.granted_by,
                        granted_at = CURRENT_TIMESTAMP
                    """,
                    (
                        guild_id,
                        application["user_id"],
                        kind,
                        str(interaction.user.id),
                    ),
                )

            audit(
                conn,
                guild_id,
                interaction.user.id,
                "official.staff.review",
                f"staff_application:{application_id}",
                {
                    "approved": approve,
                    "staff_type": kind,
                    "user_id": application["user_id"],
                    "reason": reason[:500],
                },
            )

    except Exception:
        if added_role and member and role:
            try:
                await member.remove_roles(
                    role,
                    reason="審核寫入失敗，復原授權",
                )
            except discord.HTTPException:
                LOG.exception(
                    "Discord 身分組復原失敗"
                )
        raise

    return application


async def revoke_grant(interaction, user_id, kind):
    require_owner(interaction)

    guild = interaction.guild
    guild_id = str(guild.id)
    user_id = str(user_id)

    if kind not in STAFF_TYPES:
        raise ValueError("職位不存在。")

    with transaction() as conn:
        changed = conn.execute(
            """
            DELETE FROM official_staff_grants
            WHERE guild_id = ?
              AND user_id = ?
              AND staff_type = ?
            """,
            (guild_id, user_id, kind),
        )

        if changed.rowcount != 1:
            raise ValueError(
                "此人目前沒有該職位的正式授權。"
            )

        audit(
            conn,
            guild_id,
            interaction.user.id,
            "official.staff.revoke",
            f"user:{user_id}",
            {"staff_type": kind},
        )

    role_id = get_config(guild_id).get(kind)

    if not role_id:
        return

    role = guild.get_role(int(role_id))

    if role is None:
        return

    try:
        member = await fetch_member(
            guild,
            user_id,
        )
    except ValueError:
        return

    if role in member.roles:
        try:
            await member.remove_roles(
                role,
                reason="官方人員職位撤銷",
            )
        except discord.HTTPException:
            LOG.exception(
                "撤銷資料庫授權成功，但身分組移除失敗"
            )
            raise RuntimeError(
                "資料庫授權已撤銷，"
                "但 Discord 身分組移除失敗，"
                "請管理員手動檢查。"
            )


# ==================================================
# 共用互動介面
# ==================================================

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


class PrivateView(discord.ui.View):
    def __init__(self, interaction):
        super().__init__(timeout=TIMEOUT)
        self.user_id = str(interaction.user.id)
        self.guild_id = str(interaction.guild_id)

    async def interaction_check(self, interaction):
        try:
            require_same_user(
                interaction,
                self.user_id,
                self.guild_id,
            )
            return True
        except PermissionError as exc:
            await send_error(interaction, exc)
            return False


class OwnerView(PrivateView):
    async def interaction_check(self, interaction):
        if not await super().interaction_check(
            interaction
        ):
            return False

        try:
            require_owner(interaction)
            return True
        except PermissionError as exc:
            await send_error(interaction, exc)
            return False



class ApplyModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(
            title=f"申請{STAFF_TYPES[kind]}"
        )

        self.kind = kind

        self.nickname = discord.ui.TextInput(
            label="身分專用暱稱",
            placeholder="請填寫擔任此職位時使用的暱稱",
            style=discord.TextStyle.short,
            min_length=2,
            max_length=32,
            required=True,
        )

        self.add_item(self.nickname)

    async def on_submit(self, interaction):
        try:
            if interaction.guild_id is None:
                raise ValueError(
                    "請在伺服器內申請。"
                )

            nickname = str(
                self.nickname.value
            ).strip()

            if not 2 <= len(nickname) <= 32:
                raise ValueError(
                    "身分專用暱稱必須介於2至32字。"
                )

            number = apply_staff(
                interaction.guild_id,
                interaction.user.id,
                self.kind,
                nickname,
            )

            await interaction.response.send_message(
                "✅ **申請成功送出**\n\n"
                f"編號：#{number}\n"
                f"申請職位：{STAFF_TYPES[self.kind]}\n"
                f"身分專用暱稱：{discord.utils.escape_mentions(nickname)}\n\n"
                "請等待伺服器擁有者審核。",
                ephemeral=True,
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class RoleBindingSelect(discord.ui.RoleSelect):
    def __init__(self, kind):
        super().__init__(
            placeholder=(
                f"選擇{STAFF_TYPES[kind]}身分組"
            ),
            min_values=1,
            max_values=1,
        )
        self.kind = kind

    async def callback(self, interaction):
        try:
            set_binding(
                interaction,
                self.kind,
                self.values[0],
            )

            await interaction.response.edit_message(
                content=(
                    "✅ **身分組設定完成**\n"
                    f"{STAFF_TYPES[self.kind]}："
                    f"{self.values[0].mention}"
                ),
                view=None,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class RoleBindingView(OwnerView):
    def __init__(self, interaction, kind):
        super().__init__(interaction)
        self.add_item(RoleBindingSelect(kind))


# ==================================================
# 待審申請
# ==================================================

class ApplicationSelect(discord.ui.Select):
    def __init__(self, result):
        items = result["items"]

        options = [
            discord.SelectOption(
                label=(
                    f"#{row['id']} "
                    f"{STAFF_TYPES[row['staff_type']]}"
                )[:100],
                description=(
                    f"申請人：{row['user_id']}"
                )[:100],
                value=str(row["id"]),
            )
            for row in items
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="沒有待審申請",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇待審申請",
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
            row = get_application(
                interaction.guild_id,
                int(self.values[0]),
            )


            nickname = discord.utils.escape_mentions(
                row["reason"]
            )


            await interaction.response.edit_message(
                content=(
                    "🛡️ **官方人員申請**\n\n"
                    f"編號：#{row['id']}\n"
                    f"申請人：<@{row['user_id']}>\n"
                    f"職位：{STAFF_TYPES[row['staff_type']]}\n"
                    f"申請時間：{row['created_at']}\n\n"
                    f"**身分專用暱稱**\n{nickname[:32]}"

                ),
                view=ApplicationDetailView(
                    interaction,
                    row["id"],
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class RejectModal(discord.ui.Modal):
    def __init__(self, application_id):
        super().__init__(
            title="駁回官方人員申請"
        )

        self.application_id = application_id

        self.reason = discord.ui.TextInput(
            label="駁回原因",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        try:
            await decide_application(
                interaction,
                self.application_id,
                False,
                str(self.reason.value),
            )

            await interaction.response.send_message(
                "✅ 申請已駁回並保存紀錄。",
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class ApplicationDetailView(OwnerView):
    def __init__(self, interaction, application_id):
        super().__init__(interaction)
        self.application_id = application_id

    @discord.ui.button(
        label="✅ 核准",
        style=discord.ButtonStyle.success,
    )
    async def approve(self, interaction, button):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            await decide_application(
                interaction,
                self.application_id,
                True,
            )

            await interaction.edit_original_response(
                content="✅ 已核准，授權與身分組已更新。",
                view=None,
            )

        except (
            PermissionError,
            ValueError,
            discord.HTTPException,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="❌ 駁回",
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction, button):
        await interaction.response.send_modal(
            RejectModal(self.application_id)
        )

    @discord.ui.button(
        label="⬅️ 返回待審清單",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(self, interaction, button):
        result = get_pending(
            interaction.guild_id
        )

        await interaction.response.edit_message(
            content=pending_text(result),
            view=PendingView(interaction),
        )


def pending_text(result):
    return (
        "🛡️ **官方人員待審申請**\n\n"
        f"待審：{result['total']}份\n"
        f"第{result['page'] + 1}/"
        f"{result['last'] + 1}頁\n\n"
        "請選擇申請並核准或駁回。"
    )


class PendingView(OwnerView):
    def __init__(self, interaction, page=0):
        super().__init__(interaction)

        result = get_pending(
            interaction.guild_id,
            page,
        )

        self.page = result["page"]
        self.last = result["last"]

        self.add_item(ApplicationSelect(result))

        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.last

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):
        result = get_pending(
            interaction.guild_id,
            self.page - 1,
        )

        await interaction.response.edit_message(
            content=pending_text(result),
            view=PendingView(
                interaction,
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
            interaction.guild_id,
            self.page + 1,
        )

        await interaction.response.edit_message(
            content=pending_text(result),
            view=PendingView(
                interaction,
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
            interaction.guild_id,
            self.page,
        )

        await interaction.response.edit_message(
            content=pending_text(result),
            view=PendingView(
                interaction,
                result["page"],
            ),
        )


# ==================================================
# 正式人員管理與撤銷
# ==================================================

class GrantSelect(discord.ui.Select):
    def __init__(self, result):
        items = result["items"]

        options = [
            discord.SelectOption(
                label=(
                    f"{STAFF_TYPES[row['staff_type']]} "
                    f"｜{row['user_id']}"
                )[:100],
                description=(
                    f"授權時間：{row['granted_at']}"
                )[:100],
                value=(
                    f"{row['staff_type']}:{row['user_id']}"
                ),
            )
            for row in items
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有正式人員",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇正式人員",
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
            kind, user_id = self.values[0].split(
                ":",
                1,
            )

            # 不相信舊選單，重新核對授權。
            with get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT *
                    FROM official_staff_grants
                    WHERE guild_id = ?
                      AND user_id = ?
                      AND staff_type = ?
                    """,
                    (
                        str(interaction.guild_id),
                        user_id,
                        kind,
                    ),
                ).fetchone()

            if row is None:
                raise ValueError(
                    "此人員授權已失效，請重新整理。"
                )

            await interaction.response.edit_message(
                content=(
                    "🏛️ **正式官方人員**\n\n"
                    f"人員：<@{user_id}>\n"
                    f"職位：{STAFF_TYPES[kind]}\n"
                    f"授權時間：{row['granted_at']}\n\n"
                    "如需撤銷，請按下方按鈕。"
                ),
                view=GrantDetailView(
                    interaction,
                    user_id,
                    kind,
                ),
            )

        except (
            ValueError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)


class GrantDetailView(OwnerView):
    def __init__(self, interaction, user_id, kind):
        super().__init__(interaction)
        self.target_user_id = user_id
        self.kind = kind

    @discord.ui.button(
        label="撤銷此職位",
        style=discord.ButtonStyle.danger,
    )
    async def revoke(self, interaction, button):
        await interaction.response.edit_message(
            content=(
                "⚠️ **確認撤銷官方職位？**\n\n"
                f"人員：<@{self.target_user_id}>\n"
                f"職位：{STAFF_TYPES[self.kind]}\n\n"
                "確認後會立即停止資料庫授權，"
                "並嘗試移除對應的 Discord 身分組。"
            ),
            view=RevokeConfirmView(
                interaction,
                self.target_user_id,
                self.kind,
            ),
        )

    @discord.ui.button(
        label="⬅️ 返回人員清單",
        style=discord.ButtonStyle.secondary,
    )
    async def back(self, interaction, button):
        result = get_grants(
            interaction.guild_id
        )

        await interaction.response.edit_message(
            content=grants_text(result),
            view=GrantsView(interaction),
        )


class RevokeConfirmView(OwnerView):
    def __init__(self, interaction, user_id, kind):
        super().__init__(interaction)
        self.target_user_id = user_id
        self.kind = kind

    @discord.ui.button(
        label="確認撤銷",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(self, interaction, button):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            await revoke_grant(
                interaction,
                self.target_user_id,
                self.kind,
            )

            await interaction.edit_original_response(
                content="✅ 官方職位已撤銷。",
                view=None,
            )

        except (
            PermissionError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="已取消撤銷操作。",
            view=None,
        )


def grants_text(result):
    return (
        "🏛️ **正式官方人員名單**\n\n"
        f"目前授權：{result['total']}項\n"
        f"第{result['page'] + 1}/"
        f"{result['last'] + 1}頁\n\n"
        "請選擇人員查看或撤銷授權。"
    )


class GrantsView(OwnerView):
    def __init__(self, interaction, page=0):
        super().__init__(interaction)

        result = get_grants(
            interaction.guild_id,
            page,
        )

        self.page = result["page"]
        self.last = result["last"]

        self.add_item(GrantSelect(result))

        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.last

    @discord.ui.button(
        label="⬅️ 上一頁",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous(self, interaction, button):
        result = get_grants(
            interaction.guild_id,
            self.page - 1,
        )

        await interaction.response.edit_message(
            content=grants_text(result),
            view=GrantsView(
                interaction,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="下一頁 ➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next(self, interaction, button):
        result = get_grants(
            interaction.guild_id,
            self.page + 1,
        )

        await interaction.response.edit_message(
            content=grants_text(result),
            view=GrantsView(
                interaction,
                result["page"],
            ),
        )

    @discord.ui.button(
        label="🔄 重新整理",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def refresh(self, interaction, button):
        result = get_grants(
            interaction.guild_id,
            self.page,
        )

        await interaction.response.edit_message(
            content=grants_text(result),
            view=GrantsView(
                interaction,
                result["page"],
            ),
        )


# ==================================================
# 官方首頁
# ==================================================

class OfficialProductModal(discord.ui.Modal):
    """官方商城商品新增與修改表單。"""

    def __init__(self, kind):
        title = (
            "官方商城新增商品"
            if kind == "add"
            else "官方商城修改商品"
        )

        super().__init__(title=title, timeout=300)
        self.kind = kind

        if kind == "add":
            fields = (
                ("商品名稱", True),
                ("商品分類", True),
                ("售價（金幣）", True),
                ("庫存（留空不限）", False),
                ("黑卡專屬（是／否，預設否）", False),
            )
        else:
            fields = (
                ("商品編號", True),
                ("新價格（留空不更改）", False),
                ("新庫存（留空不更改）", False),
                ("是否上架（是／否／留空）", False),
            )

        self.fields = []

        for label, required in fields:
            field = discord.ui.TextInput(
                label=label,
                required=required,
                max_length=100,
            )

            self.add_item(field)
            self.fields.append(field)

    async def on_submit(self, interaction):
        from core.permissions import (
            Actor,
            PermissionDenied,
            InvalidState,
        )
        from modules import shop

        actor = Actor.from_interaction(interaction)

        # 群主權限高於一般管理員，且商城底層需要 is_admin。
        if is_owner(interaction) and not actor.is_admin:
            actor = Actor(
                guild_id=actor.guild_id,
                user_id=actor.user_id,
                is_admin=True,
            )

        if not actor.is_admin:
            await interaction.response.send_message(
                "⛔ 只有群主或伺服器管理員可以管理官方商品。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            values = [
                str(field.value or "").strip()
                for field in self.fields
            ]

            if self.kind == "add":
                if values[4] not in ("", "是", "否"):
                    raise ValueError(
                        "黑卡專屬請填寫「是」或「否」。"
                    )

                product_id = shop.add_product(
                    actor,
                    values[0],
                    values[1],
                    int(values[2]),
                    int(values[3]) if values[3] else None,
                    "black" if values[4] == "是" else "public",
                    "none",
                    "",
                    0,
                    "",
                    "",
                )

                result = (
                    f"✅ 官方商品 #{product_id} 已新增。"
                )

            else:
                if values[3] not in ("", "是", "否"):
                    raise ValueError(
                        "是否上架請填寫「是」或「否」。"
                    )

                result = shop.edit_product(
                    actor,
                    int(values[0]),
                    int(values[1]) if values[1] else None,
                    int(values[2]) if values[2] else None,
                    (
                        None
                        if not values[3]
                        else values[3] == "是"
                    ),
                )

        except (
            ValueError,
            PermissionDenied,
            InvalidState,
            sqlite3.Error,
        ) as exc:
            result = f"⚠️ {exc}"

        await interaction.followup.send(
            str(result)[:1900],
            ephemeral=True,
        )



class OfficialHome(PrivateView):
    """官方人員、商城、金幣、內容建立與兌換管理中心。"""

    def __init__(self, interaction):
        super().__init__(interaction)

        from core.permissions import Actor
        from modules.official_shop import ShopManageButton
        from modules.official_test_money import MoneyManageButton

        actor = Actor.from_interaction(interaction)

        # 群主或管理員可進入商城、金幣管理
        if is_owner(interaction) or actor.is_admin:
            self.add_item(ShopManageButton())
            self.add_item(MoneyManageButton())

        # 群主專屬功能
        if is_owner(interaction):

            # ✍️ 內容建立中心
            from modules.content_builder import ContentButton
            self.add_item(ContentButton())

            # 🎁 B09 特殊道具兌換審核
            from modules.redeem_ui import RedeemManageButton
            self.add_item(RedeemManageButton())

    # 下面原有的 @discord.ui.button 全部保留

    @discord.ui.button(
        label="申請審核員",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def apply_reviewer(self, interaction, button):
        await interaction.response.send_modal(
            ApplyModal("reviewer")
        )

    @discord.ui.button(
        label="申請紀律人員",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def apply_discipline(self, interaction, button):
        await interaction.response.send_modal(
            ApplyModal("discipline")
        )

    @discord.ui.button(
        label="我的申請",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def my_applications(self, interaction, button):
        rows = get_my_applications(
            interaction.guild_id,
            interaction.user.id,
        )

        if not rows:
            message = "你目前沒有官方人員申請紀錄。"
        else:
            status_names = {
                "pending": "待審",
                "approved": "通過",
                "rejected": "駁回",
                "cancelled": "取消",
            }

            lines = ["📋 **我的官方人員申請**"]

            for row in rows:
                lines.append(
                    f"#{row['id']}｜"
                    f"{STAFF_TYPES[row['staff_type']]}｜"
                    f"{status_names.get(row['status'], row['status'])}"
                )

            message = "\n".join(lines)

        await interaction.response.send_message(
            message[:1900],
            ephemeral=True,
        )

    @discord.ui.button(
        label="待審人員申請",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def pending(self, interaction, button):
        try:
            require_owner(interaction)

            result = get_pending(
                interaction.guild_id
            )

            await interaction.response.send_message(
                pending_text(result),
                view=PendingView(interaction),
                ephemeral=True,
            )

        except PermissionError as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="正式人員管理",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def staff_management(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            result = get_grants(
                interaction.guild_id
            )

            await interaction.response.send_message(
                grants_text(result),
                view=GrantsView(interaction),
                ephemeral=True,
            )

        except PermissionError as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="設定審核員身分組",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def reviewer_role(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            await interaction.response.send_message(
                "請選擇審核員專用身分組。",
                view=RoleBindingView(
                    interaction,
                    "reviewer",
                ),
                ephemeral=True,
            )

        except PermissionError as exc:
            await send_error(interaction, exc)

    @discord.ui.button(
        label="設定紀律人員身分組",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def discipline_role(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            await interaction.response.send_message(
                "請選擇紀律人員專用身分組。",
                view=RoleBindingView(
                    interaction,
                    "discipline",
                ),
                ephemeral=True,
            )

        except PermissionError as exc:
            await send_error(interaction, exc)


class OfficialCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        init_official_tables()

    @app_commands.command(
        name="官方",
        description="官方職位申請與管理中心",
    )
    @app_commands.guild_only()
    async def official(
        self,
        interaction: discord.Interaction,
    ):
        try:
            config = get_config(
                interaction.guild_id
            )

            lines = [
                "🏛️ **官方管理中心**",
                "",
                "玩家可以申請擔任審核員或紀律人員。",
                "正式授權須由伺服器擁有者核准。",
                "",
                "🛡️ 審核員：角色與各項申請審核",
                "⚖️ 紀律人員：檢舉及違規處理",
            ]

            if is_owner(interaction):
                lines.extend([
                    "",
                    "**目前官方身分組**",
                ])

                for kind, name in STAFF_TYPES.items():
                    role_id = config.get(kind)

                    lines.append(
                        f"{name}："
                        + (
                            f"<@&{role_id}>"
                            if role_id
                            else "尚未設定"
                        )
                    )

                lines.append(
                    "\n可以從下方管理待審申請及正式人員。"
                )

            await interaction.response.send_message(
                "\n".join(lines),
                view=OfficialHome(interaction),
                ephemeral=True,
            )

        except sqlite3.Error:
            LOG.exception(
                "官方管理中心資料庫錯誤"
            )

            await send_error(
                interaction,
                "官方管理中心無法讀取資料。",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(
        OfficialCenter(bot)
    )
