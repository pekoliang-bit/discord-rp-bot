"""/公司 身分入口；公司人事與陣營完全分離。"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_connection
from modules.world_terms import term, organization_terms

from modules.company_workforce import (
    apply_job,
    configure_position,
    pending_reviews,
    resign,
    review_application,
    review_work,
    review_promotion,
    submit_work,
)

from modules.company_business import (
    initialize as init_business,
    invite_manager,
    accept_manager,
    grant_manager_permissions,
)


# ============================================================
# 公司資料查詢
# ============================================================

def company_context(gid: str, uid: str):
    gid = str(gid)
    uid = str(uid)

    init_business()

    with get_connection() as db:

        # 舊系統已核准公司
        owned = db.execute(
            """
            SELECT company_id
            FROM rp_approved_company_authorizations
            WHERE guild_id = ?
              AND original_applicant_id = ?
              AND status = 'approved'
            ORDER BY company_id
            """,
            (gid, uid),
        ).fetchall()

        # 玩家任職資料
        employees = db.execute(
            """
            SELECT
                e.company_id,
                e.character_id,
                e.rank,
                e.exp,
                COALESCE(p.title, '職位未設定') AS title
            FROM company_employees e
            JOIN characters c
                ON c.guild_id = e.guild_id
                AND c.id = e.character_id
            LEFT JOIN company_job_positions p
                ON p.guild_id = e.guild_id
                AND p.company_id = e.company_id
                AND p.rank = e.rank
            WHERE e.guild_id = ?
              AND c.owner_id = ?
              AND e.status = 'active'
            ORDER BY e.company_id, e.character_id
            """,
            (gid, uid),
        ).fetchall()

        employee_lines = []

        for employee in employees:

            next_position = db.execute(
                """
                SELECT
                    rank,
                    title,
                    exp_required,
                    promotion_mode
                FROM company_job_positions
                WHERE guild_id = ?
                  AND company_id = ?
                  AND rank > ?
                ORDER BY rank
                LIMIT 1
                """,
                (
                    gid,
                    employee["company_id"],
                    employee["rank"],
                ),
            ).fetchone()

            if next_position:

                remaining_exp = max(
                    0,
                    next_position["exp_required"]
                    - employee["exp"],
                )

                promotion_method = (
                    "達標自動升職"
                    if next_position["promotion_mode"] == "auto"
                    else "達標後由老闆審核"
                )

                next_text = (
                    f"下一級「{next_position['title']}」"
                    f"需累積 {next_position['exp_required']} EXP，"
                    f"還差 {remaining_exp} EXP，"
                    f"{promotion_method}"
                )

            else:
                next_text = "已達目前最高職位"

            all_positions = db.execute(
                """
                SELECT
                    rank,
                    title,
                    exp_required
                FROM company_job_positions
                WHERE guild_id = ?
                  AND company_id = ?
                ORDER BY rank
                LIMIT 20
                """,
                (
                    gid,
                    employee["company_id"],
                ),
            ).fetchall()

            position_track = "、".join(
                f"{position['title']} "
                f"{position['exp_required']} EXP"
                for position in all_positions
            )

            employee_lines.append(
                f"• {employee['company_id']}"
                f"｜角色 #{employee['character_id']}"
                f"｜{employee['title']}"
                f"（LV.{employee['rank']}）"
                f"｜累積 {employee['exp']} EXP\n"
                f"  {next_text}\n"
                f"  各職位累積門檻："
                f"{position_track or '未設定'}"
            )

        # 新系統公司、品牌、集團
        new_owned = db.execute(
            """
            SELECT
                business_id,
                name,
                kind,
                category
            FROM rp_business_entities
            WHERE guild_id = ?
              AND owner_id = ?
              AND status = 'active'
            ORDER BY business_id
            """,
            (gid, uid),
        ).fetchall()

        terms = organization_terms(gid)
        kind_names = {key: terms[key] for key in ("company", "brand", "group")}

        new_owned_ids = {
            row["business_id"]
            for row in new_owned
        }

        # 舊公司避免重複顯示
        old_business_list = [
            row["company_id"]
            for row in owned
            if row["company_id"] not in new_owned_ids
        ]

        new_business_list = [
            (
                f"{row['business_id']}"
                f"（{row['name']}／"
                f"{kind_names.get(row['kind'], row['kind'])}）"
            )
            for row in new_owned
        ]

        # 玩家被任命的管理職務
        managed = db.execute(
            """
            SELECT
                m.business_id,
                b.name,
                m.title
            FROM rp_business_managers m
            JOIN rp_business_entities b
                ON b.guild_id = m.guild_id
                AND b.business_id = m.business_id
            WHERE m.guild_id = ?
              AND m.user_id = ?
              AND m.status = 'active'
              AND b.status = 'active'
            ORDER BY m.business_id
            """,
            (gid, uid),
        ).fetchall()

        if managed:
            managed_text = "、".join(
                (
                    f"{row['business_id']}"
                    f"（{row['name']}／{row['title']}）"
                )
                for row in managed
            )

            employee_lines.append(
                f"**我管理的{terms['company']}／{terms['brand']}**：{managed_text}"
            )

        owned_businesses = (
            old_business_list + new_business_list
        )

        return (
            owned_businesses,
            "\n".join(employee_lines),
        )


def company_positions(gid: str, company: str):

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                rank,
                title,
                exp_required,
                exp_per_work,
                promotion_mode
            FROM company_job_positions
            WHERE guild_id = ?
              AND company_id = ?
            ORDER BY rank
            """,
            (gid, company),
        ).fetchall()

        if not rows:
            return "公司尚未設定職位。"

        return "\n".join(
            (
                f"LV.{row['rank']} "
                f"{row['title']}"
                f"｜累積 {row['exp_required']} EXP"
                f"｜工作 +{row['exp_per_work']}"
                f"｜{'自動' if row['promotion_mode'] == 'auto' else '審核'}升職"
            )
            for row in rows
        )


# ============================================================
# 共用回覆處理
# ============================================================

async def respond(interaction, action):

    await interaction.response.defer(ephemeral=True)

    try:
        result = await asyncio.to_thread(action)

    except (
        ValueError,
        PermissionError,
        sqlite3.Error,
    ) as exc:
        result = f"⚠️ {exc}"

    await interaction.followup.send(
        str(result)[:1900],
        ephemeral=True,
    )


class OwnedModal(discord.ui.Modal):

    def __init__(self, uid, gid, title):

        super().__init__(
            title=title,
            timeout=600,
        )

        self.uid = str(uid)
        self.gid = str(gid)

    async def submit_job(self, interaction, job):

        if (
            str(interaction.user.id) != self.uid
            or str(interaction.guild_id) != self.gid
        ):
            return await interaction.response.send_message(
                "這不是你的操作表單。",
                ephemeral=True,
            )

        await respond(interaction, job)


# ============================================================
# 員工應徵
# ============================================================

class ApplyModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "公司職位應徵",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )

        self.character = discord.ui.TextInput(
            label="自己角色的編號",
            max_length=20,
        )

        self.add_item(self.company)
        self.add_item(self.character)

    async def on_submit(self, interaction):

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: apply_job(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                character_id,
            ),
        )


# ============================================================
# 員工辭職
# ============================================================

class ResignModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "辭職確認",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )

        self.character = discord.ui.TextInput(
            label="自己角色的編號",
            max_length=20,
        )

        self.confirm = discord.ui.TextInput(
            label="輸入「確認辭職」",
            max_length=10,
        )

        for control in (
            self.company,
            self.character,
            self.confirm,
        ):
            self.add_item(control)

    async def on_submit(self, interaction):

        if str(self.confirm.value).strip() != "確認辭職":
            return await interaction.response.send_message(
                "尚未確認辭職，沒有更動任職狀態。",
                ephemeral=True,
            )

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: resign(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                character_id,
            ),
        )


# ============================================================
# 公司職位設定
# ============================================================

class PositionModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "公司職位與經驗設定",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )

        self.rank = discord.ui.TextInput(
            label="階級（從 0 開始）",
            max_length=3,
        )

        self.title_input = discord.ui.TextInput(
            label="職位名稱",
            max_length=60,
        )

        self.required = discord.ui.TextInput(
            label="累積升職 EXP 門檻",
            max_length=12,
        )

        self.exp_mode = discord.ui.TextInput(
            label="工作EXP,升職方式,薪資",
            placeholder="例如：20,自動,5000",
            max_length=45,
        )

        for control in (
            self.company,
            self.rank,
            self.title_input,
            self.required,
            self.exp_mode,
        ):
            self.add_item(control)

    async def on_submit(self, interaction):

        try:
            parts = [
                item.strip()
                for item in str(self.exp_mode.value).split(",")
            ]

            if len(parts) != 3:
                raise ValueError

            points, mode, salary = parts

            if mode not in ("自動", "審核"):
                raise ValueError

            rank = int(self.rank.value)
            required = int(self.required.value)
            points = int(points)
            salary = int(salary)

            if not (
                0 <= rank <= 30
                and 0 <= required <= 100_000_000
                and 0 <= points <= 100_000
                and 0 <= salary <= 100_000_000
            ):
                raise ValueError

        except ValueError:
            return await interaction.response.send_message(
                "格式錯誤：例如 20,自動,5000；數值須為非負整數。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: configure_position(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                rank,
                str(self.title_input.value),
                required,
                points,
                (
                    "auto"
                    if mode == "自動"
                    else "approval"
                ),
                salary,
            ),
        )


# ============================================================
# 人事審核
# ============================================================

class ReviewModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "公司人事審核",
        )

        self.kind = discord.ui.TextInput(
            label="種類：應徵／工作／升職",
            max_length=10,
        )

        self.number = discord.ui.TextInput(
            label="待審編號",
            max_length=20,
        )

        self.choice = discord.ui.TextInput(
            label="處理：通過／拒絕",
            max_length=10,
        )

        for control in (
            self.kind,
            self.number,
            self.choice,
        ):
            self.add_item(control)

    async def on_submit(self, interaction):

        kind = str(self.kind.value).strip()
        decision = str(self.choice.value).strip()

        if (
            kind not in ("應徵", "工作", "升職")
            or decision not in ("通過", "拒絕")
        ):
            return await interaction.response.send_message(
                "請輸入種類：應徵／工作／升職，"
                "結果：通過／拒絕。",
                ephemeral=True,
            )

        try:
            review_id = int(self.number.value)

        except ValueError:
            return await interaction.response.send_message(
                "待審編號必須是數字。",
                ephemeral=True,
            )

        actions = {
            "應徵": review_application,
            "工作": review_work,
            "升職": review_promotion,
        }

        await self.submit_job(
            interaction,
            lambda: actions[kind](
                self.gid,
                self.uid,
                review_id,
                decision == "通過",
            ),
        )


# ============================================================
# 公司營運待審清單
# ============================================================

class OwnerLookupModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "公司營運與待審",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )

        self.add_item(self.company)

    async def on_submit(self, interaction):

        await self.submit_job(
            interaction,
            lambda: pending_reviews(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
            ),
        )


# ============================================================
# 員工提交工作
# ============================================================

class WorkModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "提交公司工作",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=100,
        )

        self.character = discord.ui.TextInput(
            label="自己的角色編號",
            max_length=20,
        )

        self.description = discord.ui.TextInput(
            label="工作內容",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        self.evidence = discord.ui.TextInput(
            label="認證紀錄連結（選填）",
            required=False,
            max_length=500,
        )

        for control in (
            self.company,
            self.character,
            self.description,
            self.evidence,
        ):
            self.add_item(control)

    async def on_submit(self, interaction):

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: submit_work(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                character_id,
                str(self.description.value),
                str(self.evidence.value or ""),
            ),
        )


# ============================================================
# 管理層邀請
# ============================================================

class InviteManagerModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "邀請公司管理層",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=80,
        )

        self.member = discord.ui.TextInput(
            label="@Discord玩家（實際提及）",
            max_length=80,
        )

        self.character = discord.ui.TextInput(
            label="角色編號",
            max_length=20,
        )

        self.name = discord.ui.TextInput(
            label="角色名字",
            max_length=100,
        )

        self.position = discord.ui.TextInput(
            label="管理職稱",
            max_length=60,
            default="管理層",
        )

        for field in (
            self.company,
            self.member,
            self.character,
            self.name,
            self.position,
        ):
            self.add_item(field)

    async def on_submit(self, interaction):

        mention = re.fullmatch(
            r"<@!?(\d{15,22})>",
            str(self.member.value).strip(),
        )

        if not mention:
            return await interaction.response.send_message(
                "請輸入實際的 Discord @玩家提及。",
                ephemeral=True,
            )

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: invite_manager(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                mention.group(1),
                character_id,
                str(self.name.value),
                str(self.position.value),
            ),
        )


# ============================================================
# 接受管理層邀請
# ============================================================

class AcceptManagerModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "接受管理層邀請",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=80,
        )

        self.character = discord.ui.TextInput(
            label="自己的角色編號",
            max_length=20,
        )

        self.add_item(self.company)
        self.add_item(self.character)

    async def on_submit(self, interaction):

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        await self.submit_job(
            interaction,
            lambda: accept_manager(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                character_id,
            ),
        )


# ============================================================
# 管理層權限設定
# ============================================================

class ManagerPermissionsModal(OwnedModal):

    def __init__(self, uid, gid):

        super().__init__(
            uid,
            gid,
            "管理層權限設定",
        )

        self.company = discord.ui.TextInput(
            label="公司編號",
            max_length=80,
        )

        self.character = discord.ui.TextInput(
            label="已接受任命的角色編號",
            max_length=20,
        )

        self.permissions = discord.ui.TextInput(
            label="權限代碼（逗號分隔）",
            max_length=200,
            placeholder=(
                "recruit,work_review,finance,products,"
                "brand,vip,endorsement,public_relations"
            ),
        )

        for field in (
            self.company,
            self.character,
            self.permissions,
        ):
            self.add_item(field)

    async def on_submit(self, interaction):

        try:
            character_id = int(self.character.value)

        except ValueError:
            return await interaction.response.send_message(
                "角色編號必須是數字。",
                ephemeral=True,
            )

        permissions = [
            item.strip()
            for item in str(self.permissions.value).split(",")
            if item.strip()
        ]

        await self.submit_job(
            interaction,
            lambda: grant_manager_permissions(
                self.gid,
                str(self.company.value).strip(),
                self.uid,
                character_id,
                permissions,
            ),
        )


# ============================================================
# 公司互動面板
# ============================================================

class CompanyPanel(discord.ui.View):

    def __init__(self, uid, gid, is_owner):

        super().__init__(timeout=600)

        self.uid = str(uid)
        self.gid = str(gid)
        self.is_owner = is_owner

        # 不擁有公司但獲得招募／工作審核授權的管理者，也要看得到入口。
        with get_connection() as db:
            rows = db.execute(
                """SELECT permissions FROM rp_business_managers
                   WHERE guild_id = ? AND user_id = ? AND status = 'active'""",
                (self.gid, self.uid),
            ).fetchall()
        can_review = is_owner or any(
            bool({"recruit", "work_review"} & set(json.loads(row["permissions"] or "[]")))
            for row in rows
        )

        # 財務、品牌與 VIP 都有獨立授權判斷；非持有人但獲授權的
        # 管理者也必須能從同一個 /公司 面板進入。
        from modules.company_treasury_ui import CompanyTreasuryButton
        from modules.company_brand_ui import BrandManagementButton
        from modules.company_vip_ui import CompanyVIPButton

        for button in (
            CompanyTreasuryButton(),
            BrandManagementButton(),
            CompanyVIPButton(),
        ):
            button.row = 3
            self.add_item(button)

        if is_owner or can_review:
            owner_actions = []
            if is_owner:
                owner_actions.append((
                    "⚙️ 職位設定", PositionModal, discord.ButtonStyle.primary,
                ))
            if can_review:
                owner_actions.extend((
                (
                    "📋 待審清單",
                    OwnerLookupModal,
                    discord.ButtonStyle.secondary,
                ),
                (
                    "✅ 人事審核",
                    ReviewModal,
                    discord.ButtonStyle.success,
                ),
                ))

            for label, modal_class, style in owner_actions:

                button = discord.ui.Button(
                    label=label,
                    style=style,
                    row=0,
                )

                async def owner_callback(
                    interaction,
                    selected_modal=modal_class,
                ):
                    await interaction.response.send_modal(
                        selected_modal(
                            self.uid,
                            self.gid,
                        )
                    )

                button.callback = owner_callback
                self.add_item(button)

        if is_owner:
            # 任命與授權變更只由負責人操作。
            manager_actions = (
                (
                    "👥 任命管理層",
                    InviteManagerModal,
                ),
                (
                    "🔐 設定授權",
                    ManagerPermissionsModal,
                ),
            )

            for label, modal_class in manager_actions:

                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.secondary,
                    row=3,
                )

                async def manager_callback(
                    interaction,
                    selected_modal=modal_class,
                ):
                    await interaction.response.send_modal(
                        selected_modal(
                            self.uid,
                            self.gid,
                        )
                    )

                button.callback = manager_callback
                self.add_item(button)

    async def interaction_check(self, interaction):

        if (
            str(interaction.user.id) != self.uid
            or str(interaction.guild_id) != self.gid
        ):
            await interaction.response.send_message(
                "這不是你的公司介面。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="👔 應徵",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def apply(self, interaction, button):

        await interaction.response.send_modal(
            ApplyModal(
                self.uid,
                self.gid,
            )
        )

    @discord.ui.button(
        label="🚪 辭職",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def leave(self, interaction, button):

        await interaction.response.send_modal(
            ResignModal(
                self.uid,
                self.gid,
            )
        )

    @discord.ui.button(
        label="📝 工作提交",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def submit(self, interaction, button):

        await interaction.response.send_modal(
            WorkModal(
                self.uid,
                self.gid,
            )
        )

    @discord.ui.button(
        label="🔄 更新資料",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def refresh(self, interaction, button):

        owned, employees = await asyncio.to_thread(
            company_context,
            self.gid,
            self.uid,
        )

        await interaction.response.edit_message(
            content=panel_text(
                owned,
                employees,
                interaction.guild_id,
            )[:1900],
            view=CompanyPanel(
                self.uid,
                self.gid,
                bool(owned),
            ),
        )

    @discord.ui.button(
        label="✅ 接受管理任命",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def accept_invite(self, interaction, button):

        await interaction.response.send_modal(
            AcceptManagerModal(
                self.uid,
                self.gid,
            )
        )


# ============================================================
# 面板文字
# ============================================================

def panel_text(owned, employees, guild_id=None):
    """只替換面板顯示詞，不修改斜線指令、資料庫類型或玩家資料。"""
    labels = organization_terms(guild_id) if guild_id is not None else {
        "company": "公司", "brand": "品牌", "group": "集團",
        "faction": "陣營", "position": "職位",
    }
    def display(message):
        # 避免連續替換造成自訂名稱被二次改寫。
        import re
        mapping = {"公司": labels["company"], "品牌": labels["brand"],
                   "集團": labels["group"], "陣營": labels["faction"],
                   "職位": labels["position"]}
        return re.sub("公司|品牌|集團|陣營|職位", lambda match: mapping[match.group()], message)


    if owned:

        company_list = "、".join(owned)

        return display((
            "🏢 **公司營運中心**\n"
            f"你持有的正式公司／品牌／集團：{company_list}\n"
            "可管理職位、待審案件與人事，"
            "也可以設定管理層授權。\n"
            "下方亦可操作個人的應徵、工作與辭職。\n\n"
            "**我的任職公司與累積經驗**\n"
            f"{employees or '目前沒有任職公司。'}\n\n"
            "注意：公司僱傭身分不會改變任何陣營身分。"
        ))

    return display((
        "👔 **玩家公司中心**\n"
        "可應徵、辭職、提交工作，"
        "並查看所屬公司與職位進度。\n"
        "若收到管理層邀請，可以點擊接受任命。\n\n"
        "**我的任職公司與各職位經驗**\n"
        f"{employees or '目前沒有任職公司。'}\n\n"
        "EXP 一律累加，升職和辭職都不會歸零。\n"
        "公司任職不代表加入該公司陣營。"
    ))


# ============================================================
# Discord /公司 指令
# ============================================================

class CompanyPortal(commands.Cog):

    @app_commands.command(
        name="公司",
        description="依身分進入公司營運或員工管理中心",
    )
    @app_commands.guild_only()
    async def company(self, interaction: discord.Interaction):

        await interaction.response.defer(
            ephemeral=True,
        )

        try:
            owned, employees = await asyncio.to_thread(
                company_context,
                str(interaction.guild_id),
                str(interaction.user.id),
            )

        except sqlite3.Error:
            return await interaction.followup.send(
                "公司資料讀取失敗，請聯絡管理員。",
                ephemeral=True,
            )

        await interaction.followup.send(
            panel_text(
                owned,
                employees,
                interaction.guild_id,
            )[:1900],
            view=CompanyPanel(
                str(interaction.user.id),
                str(interaction.guild_id),
                bool(owned),
            ),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(
        CompanyPortal()
    )
