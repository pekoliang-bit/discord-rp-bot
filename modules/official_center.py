
"""Discord RP 官方人員與權限管理中心。

正式指令：/官方

功能：
- 審核員與紀律人員申請
- 伺服器擁有者設定對應身分組
- 待審申請查詢
- 核准與駁回
- 正式授權與撤銷
- Discord 身分組同步
- 資料庫稽核紀錄
- 防止跨伺服器操作
- 供其他模組使用的權限檢查函式

注意：
這是官方人員權限核心。
角色審核、檢舉等功能須呼叫本模組的
require_staff_permission() 才會受到此權限限制。
"""

from __future__ import annotations

import json
import logging
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import (
    get_connection,
    transaction,
)

LOG = logging.getLogger(__name__)

STAFF_TYPES = {
    "reviewer": "審核員",
    "discipline": "紀律人員",
}

PAGE_SIZE = 25


def init_official_tables():
    """建立正式人員設定與申請資料表。"""

    with transaction() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_role_config (
                guild_id TEXT NOT NULL,
                staff_type TEXT NOT NULL,
                role_id TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, staff_type),
                CHECK (
                    staff_type IN (
                        'reviewer',
                        'discipline'
                    )
                )
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_staff_applications (
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
                        'reviewer',
                        'discipline'
                    )
                ),
                CHECK (
                    status IN (
                        'pending',
                        'approved',
                        'rejected',
                        'cancelled'
                    )
                )
            )
            """
        )

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_official_pending_application
            ON official_staff_applications (
                guild_id,
                user_id,
                staff_type
            )
            WHERE status = 'pending'
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_staff_grants (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                staff_type TEXT NOT NULL,
                granted_by TEXT NOT NULL,
                granted_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    guild_id,
                    user_id,
                    staff_type
                ),
                CHECK (
                    staff_type IN (
                        'reviewer',
                        'discipline'
                    )
                )
            )
            """
        )


def audit(
    conn,
    guild_id,
    actor_id,
    action,
    target,
    details,
):
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
            str(guild_id),
            str(actor_id),
            action,
            target,
            json.dumps(
                details,
                ensure_ascii=False,
            ),
        ),
    )


def is_owner(interaction):
    """只有實際伺服器擁有者擁有最高授權。"""

    guild = interaction.guild

    return (
        guild is not None
        and guild.owner_id == interaction.user.id
    )


def require_owner(interaction):
    if not is_owner(interaction):
        raise PermissionError(
            "此操作僅限伺服器擁有者。"
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
        row["staff_type"]: row["role_id"]
        for row in rows
    }


def has_staff_permission(
    interaction: discord.Interaction,
    staff_type: str,
) -> bool:
    """供審核、紀律等模組共用。

    同時驗證：
    1. 是同一個 Discord 伺服器
    2. 已獲得正式授權
    3. Discord 身分組仍然存在
    4. 操作者仍持有該身分組

    伺服器擁有者可使用全部官方權限。
    """

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
            (
                guild_id,
                user_id,
                staff_type,
            ),
        ).fetchone()

        config = conn.execute(
            """
            SELECT role_id
            FROM official_role_config
            WHERE guild_id = ?
              AND staff_type = ?
            """,
            (
                guild_id,
                staff_type,
            ),
        ).fetchone()

    if grant is None or config is None:
        return False

    role_id = int(config["role_id"])

    return any(
        role.id == role_id
        for role in member.roles
    )


def require_staff_permission(
    interaction: discord.Interaction,
    staff_type: str,
):
    if not has_staff_permission(
        interaction,
        staff_type,
    ):
        name = STAFF_TYPES.get(
            staff_type,
            "官方人員",
        )

        raise PermissionError(
            f"此功能僅限獲授權的{name}"
            "或伺服器擁有者使用。"
        )


def apply_staff(
    guild_id,
    user_id,
    staff_type,
    reason,
):
    if staff_type not in STAFF_TYPES:
        raise ValueError("申請職位不正確。")

    reason = reason.strip()

    if not 10 <= len(reason) <= 1000:
        raise ValueError(
            "申請理由必須介於10至1000字。"
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
                staff_type,
            ),
        ).fetchone()

        if existing:
            raise ValueError(
                "你已經擁有這項正式職位。"
            )

        try:
            cursor = conn.execute(
                """
                INSERT INTO official_staff_applications (
                    guild_id,
                    user_id,
                    staff_type,
                    reason
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(guild_id),
                    str(user_id),
                    staff_type,
                    reason,
                ),
            )

        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "你已有同職位的待審申請。"
            ) from exc

        audit(
            conn,
            guild_id,
            user_id,
            "official.staff.apply",
            f"staff_application:{cursor.lastrowid}",
            {
                "staff_type": staff_type,
            },
        )

        return cursor.lastrowid


def pending_applications(guild_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM official_staff_applications
            WHERE guild_id = ?
              AND status = 'pending'
            ORDER BY id ASC
            """,
            (str(guild_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def staff_overview(guild_id):
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT user_id, staff_type
            FROM official_staff_grants
            WHERE guild_id = ?
            ORDER BY staff_type, user_id
            """,
            (str(guild_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def set_role_binding(
    guild_id,
    actor_id,
    staff_type,
    role_id,
):
    if staff_type not in STAFF_TYPES:
        raise ValueError("未知的官方職位。")

    with transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO guilds(guild_id)
            VALUES (?)
            """,
            (str(guild_id),),
        )

        conn.execute(
            """
            INSERT INTO official_role_config (
                guild_id,
                staff_type,
                role_id,
                updated_by
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, staff_type)
            DO UPDATE SET
                role_id = excluded.role_id,
                updated_by = excluded.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(guild_id),
                staff_type,
                str(role_id),
                str(actor_id),
            ),
        )

        audit(
            conn,
            guild_id,
            actor_id,
            "official.staff.role_bind",
            f"staff_type:{staff_type}",
            {"role_id": str(role_id)},
        )


async def review_application(
    interaction,
    application_id,
    approve,
    reason="",
):
    """核准或駁回人員申請。

    Discord 身分組變更成功後才寫入核准紀錄。
    資料庫操作失敗時，嘗試復原新增的身分組。
    """

    require_owner(interaction)

    guild = interaction.guild
    guild_id = str(guild.id)
    reviewer_id = str(interaction.user.id)

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
                guild_id,
                int(application_id),
            ),
        ).fetchone()

    if row is None:
        raise ValueError(
            "申請不存在或已被處理。"
        )

    application = dict(row)
    staff_type = application["staff_type"]

    role = None
    member = None
    added_role = False

    if approve:
        config = get_config(guild_id)

        role_id = config.get(staff_type)

        if not role_id:
            raise ValueError(
                "請先設定這個職位的 Discord 身分組。"
            )

        role = guild.get_role(int(role_id))

        if role is None:
            raise ValueError(
                "設定的身分組已不存在。"
            )

        me = guild.me

        if (
            me is None
            or not me.guild_permissions.manage_roles
            or role >= me.top_role
        ):
            raise PermissionError(
                "機器人沒有管理此身分組的權限。"
                "請調整身分組階級。"
            )

        member = guild.get_member(
            int(application["user_id"])
        )

        if member is None:
            try:
                member = await guild.fetch_member(
                    int(application["user_id"])
                )
            except discord.NotFound as exc:
                raise ValueError(
                    "申請人已不在伺服器內。"
                ) from exc

        if role not in member.roles:
            await member.add_roles(
                role,
                reason=(
                    f"官方人員申請 #{application_id}"
                    " 已核准"
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
                    (
                        "approved"
                        if approve
                        else "rejected"
                    ),
                    reviewer_id,
                    reason[:500],
                    guild_id,
                    int(application_id),
                ),
            )

            if changed.rowcount != 1:
                raise ValueError(
                    "申請已被其他操作處理。"
                )

            if approve:
                conn.execute(
                    """
                    INSERT INTO official_staff_grants (
                        guild_id,
                        user_id,
                        staff_type,
                        granted_by
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (
                        guild_id,
                        user_id,
                        staff_type
                    )
                    DO UPDATE SET
                        granted_by = excluded.granted_by,
                        granted_at = CURRENT_TIMESTAMP
                    """,
                    (
                        guild_id,
                        application["user_id"],
                        staff_type,
                        reviewer_id,
                    ),
                )

            audit(
                conn,
                guild_id,
                reviewer_id,
                "official.staff.review",
                f"staff_application:{application_id}",
                {
                    "approved": approve,
                    "staff_type": staff_type,
                    "user_id": application["user_id"],
                    "reason": reason[:500],
                },
            )

    except Exception:
        if added_role and member and role:
            try:
                await member.remove_roles(
                    role,
                    reason="申請紀錄寫入失敗，復原授權",
                )
            except discord.HTTPException:
                LOG.exception(
                    "身分組復原失敗，需要人工檢查"
                )
        raise

    return application


async def revoke_staff(
    interaction,
    user_id,
    staff_type,
):
    """撤銷授權。先刪除資料庫授權，再移除身分組。

    即使移除 Discord 身分組失敗，
    資料庫授權已失效，仍無法使用官方功能。
    """

    require_owner(interaction)

    if staff_type not in STAFF_TYPES:
        raise ValueError("未知職位。")

    guild = interaction.guild
    guild_id = str(guild.id)

    with transaction() as conn:
        changed = conn.execute(
            """
            DELETE FROM official_staff_grants
            WHERE guild_id = ?
              AND user_id = ?
              AND staff_type = ?
            """,
            (
                guild_id,
                str(user_id),
                staff_type,
            ),
        )

        if changed.rowcount == 0:
            raise ValueError(
                "此使用者沒有該職位的正式授權。"
            )

        audit(
            conn,
            guild_id,
            interaction.user.id,
            "official.staff.revoke",
            f"user:{user_id}",
            {"staff_type": staff_type},
        )

    config = get_config(guild_id)
    role_id = config.get(staff_type)

    if not role_id:
        return

    role = guild.get_role(int(role_id))
    member = guild.get_member(int(user_id))

    if member is None:
        try:
            member = await guild.fetch_member(
                int(user_id)
            )
        except discord.NotFound:
            return

    if role and role in member.roles:
        # 若兩種職位綁定同一身分組，
        # 不可移除另一項仍有效授權所需的身分組。
        other = (
            "discipline"
            if staff_type == "reviewer"
            else "reviewer"
        )

        other_config = config.get(other)

        if other_config == role_id:
            with get_connection() as conn:
                still_granted = conn.execute(
                    """
                    SELECT 1
                    FROM official_staff_grants
                    WHERE guild_id = ?
                      AND user_id = ?
                      AND staff_type = ?
                    """,
                    (
                        guild_id,
                        str(user_id),
                        other,
                    ),
                ).fetchone()

            if still_granted:
                return

        await member.remove_roles(
            role,
            reason="官方人員職位已撤銷",
        )


async def error_response(interaction, exc):
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


class StaffApplyModal(discord.ui.Modal):
    def __init__(self, staff_type):
        super().__init__(
            title=(
                f"申請擔任{STAFF_TYPES[staff_type]}"
            )
        )
        self.staff_type = staff_type

        self.reason = discord.ui.TextInput(
            label="申請理由",
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=1000,
            required=True,
            placeholder="請說明經驗、申請動機及可協助的工作",
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        try:
            application_id = apply_staff(
                interaction.guild_id,
                interaction.user.id,
                self.staff_type,
                str(self.reason.value),
            )

            await interaction.response.send_message(
                "✅ 申請已送出。\n"
                f"申請編號：#{application_id}\n"
                f"職位：{STAFF_TYPES[self.staff_type]}\n"
                "請等待伺服器擁有者審核。",
                ephemeral=True,
            )

        except (ValueError, sqlite3.Error) as exc:
            await error_response(interaction, exc)


class StaffApplicationSelect(discord.ui.Select):
    def __init__(self, applications):
        self.applications = applications

        options = [
            discord.SelectOption(
                label=(
                    f"#{row['id']} "
                    f"{STAFF_TYPES[row['staff_type']]}"
                ),
                description=(
                    f"Discord 使用者：{row['user_id']}"
                )[:100],
                value=str(row["id"]),
            )
            for row in applications[:25]
        ]

        if not options:
            options = [
                discord.SelectOption(
                    label="目前沒有待審申請",
                    value="empty",
                )
            ]

        super().__init__(
            placeholder="選擇待審人員申請",
            options=options,
            disabled=not bool(applications),
        )

    async def callback(self, interaction):
        try:
            require_owner(interaction)

            application_id = int(self.values[0])

            rows = pending_applications(
                interaction.guild_id
            )

            application = next(
                (
                    row for row in rows
                    if row["id"] == application_id
                ),
                None,
            )

            if application is None:
                raise ValueError(
                    "申請已處理，請重新整理。"
                )

            content = (
                "🛡️ **官方人員申請審核**\n\n"
                f"編號：#{application_id}\n"
                f"申請人：<@{application['user_id']}>\n"
                f"職位：{STAFF_TYPES[application['staff_type']]}\n\n"
                "**申請理由**\n"
                f"{discord.utils.escape_mentions(application['reason'])}"
            )

            await interaction.response.edit_message(
                content=content[:1900],
                view=StaffReviewView(application_id),
            )

        except (PermissionError, ValueError) as exc:
            await error_response(interaction, exc)


class StaffReviewView(discord.ui.View):
    def __init__(self, application_id):
        super().__init__(timeout=600)
        self.application_id = application_id

    async def interaction_check(self, interaction):
        if not is_owner(interaction):
            await error_response(
                interaction,
                "只有伺服器擁有者可以審核。",
            )
            return False

        return True

    @discord.ui.button(
        label="核准申請",
        style=discord.ButtonStyle.success,
    )
    async def approve(self, interaction, button):
        await interaction.response.defer(
            ephemeral=True
        )

        try:
            await review_application(
                interaction,
                self.application_id,
                True,
            )

            await interaction.edit_original_response(
                content=(
                    "✅ 申請已核准，"
                    "正式權限及身分組已更新。"
                ),
                view=None,
            )

        except (
            PermissionError,
            ValueError,
            discord.HTTPException,
            sqlite3.Error,
        ) as exc:
            await error_response(interaction, exc)

    @discord.ui.button(
        label="駁回申請",
        style=discord.ButtonStyle.danger,
    )
    async def reject(self, interaction, button):
        await interaction.response.send_modal(
            StaffRejectModal(self.application_id)
        )


class StaffRejectModal(discord.ui.Modal):
    def __init__(self, application_id):
        super().__init__(title="駁回官方人員申請")
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
            await review_application(
                interaction,
                self.application_id,
                False,
                str(self.reason.value),
            )

            await interaction.response.send_message(
                "✅ 申請已駁回，並保留審核紀錄。",
                ephemeral=True,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await error_response(interaction, exc)


class StaffRoleSelect(discord.ui.RoleSelect):
    def __init__(self, staff_type):
        super().__init__(
            placeholder=(
                f"選擇{STAFF_TYPES[staff_type]}身分組"
            ),
            min_values=1,
            max_values=1,
        )
        self.staff_type = staff_type

    async def callback(self, interaction):
        try:
            require_owner(interaction)

            role = self.values[0]

            if role.is_default() or role.managed:
                raise ValueError(
                    "不能綁定 @everyone 或整合服務身分組。"
                )

            if role.permissions.administrator:
                raise ValueError(
                    "不可使用具有 Administrator 權限的身分組。"
                )

            me = interaction.guild.me

            if (
                me is None
                or not me.guild_permissions.manage_roles
                or role >= me.top_role
            ):
                raise PermissionError(
                    "機器人無法管理這個身分組，"
                    "請先調整 Discord 身分組階級。"
                )

            set_role_binding(
                interaction.guild_id,
                interaction.user.id,
                self.staff_type,
                role.id,
            )

            await interaction.response.edit_message(
                content=(
                    "✅ 已設定官方職位身分組。\n"
                    f"職位：{STAFF_TYPES[self.staff_type]}\n"
                    f"身分組：{role.mention}"
                ),
                view=None,
            )

        except (
            PermissionError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            await error_response(interaction, exc)


class StaffRoleView(discord.ui.View):
    def __init__(self, staff_type):
        super().__init__(timeout=600)
        self.add_item(StaffRoleSelect(staff_type))


class OfficialHome(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(
        label="申請審核員",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def apply_reviewer(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            StaffApplyModal("reviewer")
        )

    @discord.ui.button(
        label="申請紀律人員",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def apply_discipline(
        self,
        interaction,
        button,
    ):
        await interaction.response.send_modal(
            StaffApplyModal("discipline")
        )

    @discord.ui.button(
        label="待審人員申請",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def pending(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            applications = pending_applications(
                interaction.guild_id
            )

            await interaction.response.send_message(
                (
                    "🛡️ **官方人員待審申請**\n\n"
                    f"目前共有 {len(applications)} 份待審。\n"
                    "下拉選單顯示最早的25份申請。"
                ),
                view=StaffPendingView(applications),
                ephemeral=True,
            )

        except PermissionError as exc:
            await error_response(interaction, exc)

    @discord.ui.button(
        label="設定審核員身分組",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def configure_reviewer(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            await interaction.response.send_message(
                "請選擇審核員專用身分組。",
                view=StaffRoleView("reviewer"),
                ephemeral=True,
            )

        except PermissionError as exc:
            await error_response(interaction, exc)

    @discord.ui.button(
        label="設定紀律人員身分組",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def configure_discipline(
        self,
        interaction,
        button,
    ):
        try:
            require_owner(interaction)

            await interaction.response.send_message(
                "請選擇紀律人員專用身分組。",
                view=StaffRoleView("discipline"),
                ephemeral=True,
            )

        except PermissionError as exc:
            await error_response(interaction, exc)


class StaffPendingView(discord.ui.View):
    def __init__(self, applications):
        super().__init__(timeout=600)
        self.add_item(
            StaffApplicationSelect(applications)
        )


class OfficialCenter(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        init_official_tables()

    @app_commands.command(
        name="官方",
        description="官方人員申請、權限與管理中心",
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
                "一般玩家可以申請擔任官方人員。",
                "職位核准、身分組設定僅限伺服器擁有者。",
                "",
                "**正式職位**",
                "🛡️ 審核員：處理角色及其他申請",
                "⚖️ 紀律人員：處理檢舉與違規案件",
            ]

            if is_owner(interaction):
                lines.extend([
                    "",
                    "**目前身分組設定**",
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
                    "\n身分組設定後，"
                    "即可核准對應職位申請。"
                )

            await interaction.response.send_message(
                "\n".join(lines),
                view=OfficialHome(),
                ephemeral=True,
            )

        except sqlite3.Error:
            LOG.exception("官方中心讀取失敗")

            await error_response(
                interaction,
                "官方中心無法讀取資料庫。",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(OfficialCenter(bot))
