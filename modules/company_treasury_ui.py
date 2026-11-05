"""公司金庫 Discord 操作介面。

正式入口：/公司 → 金庫管理

提供：
- 多公司選擇
- 查看餘額和最近異動
- 公司／角色轉帳
- 持有人設定管理層單筆支出上限

不新增獨立 Slash Command。
"""

from __future__ import annotations

import asyncio
import sqlite3

import discord

from core.database import get_connection
from modules.company_business import has_permission
from modules.world_terms import organization_terms
from modules.company_profit import profit_overview
from modules.company_treasury import (
    company_bank_summary,
    finance_summary,
    initialize_treasury,
    move_company_bank,
    pay_employee_salary,
    set_manager_spending_limit,
    transfer_treasury,
)


def manageable_finance_businesses(guild_id, user_id):
    initialize_treasury()

    with get_connection() as db:
        rows = db.execute("""
            SELECT business_id, name, kind, owner_id
            FROM rp_business_entities
            WHERE guild_id = ?
              AND status = 'active'
            ORDER BY name
        """, (str(guild_id),)).fetchall()

        result = []

        for row in rows:
            authorized = (
                row["owner_id"] == str(user_id)
                or has_permission(
                    db,
                    str(guild_id),
                    row["business_id"],
                    str(user_id),
                    "finance",
                )
            )

            if authorized:
                result.append({
                    "business_id": row["business_id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "is_owner": (
                        row["owner_id"] == str(user_id)
                    ),
                })

        return result


async def _respond(interaction, operation):
    await interaction.response.defer(ephemeral=True)

    try:
        result = await asyncio.to_thread(operation)

    except (
        ValueError,
        PermissionError,
        sqlite3.Error,
    ) as exc:
        result = f"⚠️ {exc}"

    except Exception:
        result = (
            "⚠️ 財務操作失敗，請查看機器人紀錄。"
            "本次操作結果請以正式帳本為準。"
        )

    await interaction.followup.send(
        str(result)[:1900],
        ephemeral=True,
    )


class FinanceModal(discord.ui.Modal):
    def __init__(self, user_id, guild_id, business_id, title):
        super().__init__(title=title, timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)
        self.business_id = str(business_id)

    async def run_operation(self, interaction, operation):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            return await interaction.response.send_message(
                "這不是你的財務操作表單。",
                ephemeral=True,
            )

        await _respond(interaction, operation)


class CompanyBankMoveModal(FinanceModal):
    def __init__(self, user_id, guild_id, business_id, direction):
        self.direction = direction
        super().__init__(
            user_id, guild_id, business_id,
            "公司銀行存款" if direction == "deposit" else "公司銀行提款",
        )
        self.amount = discord.ui.TextInput(
            label="金額（金幣）", max_length=15, placeholder="例如：50000",
        )
        self.confirm = discord.ui.TextInput(
            label="輸入「確認存款」或「確認提款」", max_length=6,
        )
        self.add_item(self.amount)
        self.add_item(self.confirm)

    async def on_submit(self, interaction):
        expected = "確認存款" if self.direction == "deposit" else "確認提款"
        if str(self.confirm.value).strip() != expected:
            return await interaction.response.send_message("未輸入正確確認文字，操作已取消。", ephemeral=True)
        await self.run_operation(
            interaction,
            lambda: move_company_bank(
                self.guild_id, self.business_id, self.user_id,
                self.direction, self.amount.value,
                f"company-bank-move:{interaction.id}",
            ),
        )



class PayrollCycleModal(FinanceModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(user_id, guild_id, business_id, "設定發薪週期")
        self.cycle = discord.ui.TextInput(label="週期天數（7 或 14）", max_length=2, placeholder="7")
        self.add_item(self.cycle)

    async def on_submit(self, interaction):
        from core.company_payroll_service import configure_payroll_cycle
        try:
            days = int(str(self.cycle.value).strip())
        except ValueError:
            return await interaction.response.send_message("請輸入 7 或 14。", ephemeral=True)
        await self.run_operation(interaction, lambda: configure_payroll_cycle(
            self.guild_id, self.business_id, self.user_id, days))

class PayrollModal(FinanceModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(user_id, guild_id, business_id, "核發到期員工薪資")
        self.character_id = discord.ui.TextInput(
            label="收薪員工角色編號", max_length=20,
            placeholder="填寫正式角色編號，例如 123",
        )
        self.confirm = discord.ui.TextInput(
            label="請輸入「確認發薪」", max_length=8,
        )
        self.add_item(self.character_id)
        self.add_item(self.confirm)

    async def on_submit(self, interaction):
        if str(self.confirm.value).strip() != "確認發薪":
            return await interaction.response.send_message(
                "未輸入確認文字，薪資未發放。", ephemeral=True,
            )
        await self.run_operation(
            interaction,
            lambda: pay_employee_salary(
                self.guild_id, self.business_id, self.user_id,
                self.character_id.value,
            ),
        )


class TreasuryTransferModal(FinanceModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(
            user_id,
            guild_id,
            business_id,
            "公司資金調度",
        )

        self.target_type = discord.ui.TextInput(
            label="收款類型：公司／角色",
            placeholder="例如：公司",
            max_length=5,
        )

        self.target_id = discord.ui.TextInput(
            label="收款公司編號或角色編號",
            max_length=100,
        )

        self.amount = discord.ui.TextInput(
            label="轉帳金額（金幣）",
            placeholder="例如：50000",
            max_length=15,
        )

        self.reason = discord.ui.TextInput(
            label="支出用途",
            placeholder="例如：支付品牌宣傳費用",
            max_length=300,
        )

        self.confirm = discord.ui.TextInput(
            label="輸入「確認轉帳」",
            max_length=10,
        )

        for item in (
            self.target_type,
            self.target_id,
            self.amount,
            self.reason,
            self.confirm,
        ):
            self.add_item(item)

    async def on_submit(self, interaction):
        if self.confirm.value.strip() != "確認轉帳":
            return await interaction.response.send_message(
                "未確認轉帳，沒有扣款。",
                ephemeral=True,
            )

        selected_type = self.target_type.value.strip()

        type_mapping = {
            "公司": "organization",
            "角色": "character",
        }

        if selected_type not in type_mapping:
            return await interaction.response.send_message(
                "收款類型請填寫「公司」或「角色」。",
                ephemeral=True,
            )

        try:
            amount = int(self.amount.value)
        except ValueError:
            return await interaction.response.send_message(
                "金額必須是整數。",
                ephemeral=True,
            )

        # Discord 互動 ID 作為交易唯一鍵。
        # 重複處理同一提交時不會重複扣款。
        transaction_key = (
            f"company-finance-ui:{interaction.id}"
        )

        await self.run_operation(
            interaction,
            lambda: transfer_treasury(
                self.guild_id,
                self.business_id,
                self.user_id,
                type_mapping[selected_type],
                self.target_id.value.strip(),
                amount,
                self.reason.value.strip(),
                unique_key=transaction_key,
            ),
        )


class SpendingLimitModal(FinanceModal):
    def __init__(self, user_id, guild_id, business_id):
        super().__init__(
            user_id,
            guild_id,
            business_id,
            "管理層支出上限設定",
        )

        self.manager = discord.ui.TextInput(
            label="管理層 Discord 玩家 ID",
            placeholder="請填數字 ID",
            max_length=25,
        )

        self.amount = discord.ui.TextInput(
            label="單筆支出上限（金幣）",
            placeholder="例如：100000",
            max_length=15,
        )

        self.confirm = discord.ui.TextInput(
            label="輸入「確認授權」",
            max_length=10,
        )

        for item in (
            self.manager,
            self.amount,
            self.confirm,
        ):
            self.add_item(item)

    async def on_submit(self, interaction):
        if self.confirm.value.strip() != "確認授權":
            return await interaction.response.send_message(
                "未確認授權，沒有修改管理層額度。",
                ephemeral=True,
            )

        try:
            manager_id = int(
                self.manager.value.strip()
            )

            amount = int(
                self.amount.value.strip()
            )

        except ValueError:
            return await interaction.response.send_message(
                "玩家 ID 與金額必須是整數。",
                ephemeral=True,
            )

        await self.run_operation(
            interaction,
            lambda: set_manager_spending_limit(
                self.guild_id,
                self.business_id,
                self.user_id,
                str(manager_id),
                amount,
            ),
        )


class ReserveModal(FinanceModal):
    def __init__(self,user_id,guild_id,business_id):
        super().__init__(user_id,guild_id,business_id,'核定分潤準備金')
        self.amount=discord.ui.TextInput(label='從公司銀行撥入金額',max_length=15)
        self.confirm=discord.ui.TextInput(label='輸入「確認撥款」',max_length=6)
        self.add_item(self.amount);self.add_item(self.confirm)
    async def on_submit(self,interaction):
        if str(self.confirm.value).strip()!='確認撥款':
            return await interaction.response.send_message('確認文字不正確，已取消。',ephemeral=True)
        from core.company_profit_reserve import allocate_profit
        await self.run_operation(interaction,lambda:allocate_profit(self.guild_id,self.business_id,self.user_id,self.amount.value,f'{interaction.id}'))

class DividendModal(FinanceModal):
    def __init__(self,user_id,guild_id,business_id):
        super().__init__(user_id,guild_id,business_id,'發放公司分潤')
        self.cid=discord.ui.TextInput(label='收款角色 ID',max_length=15)
        self.amount=discord.ui.TextInput(label='分潤金額',max_length=15)
        self.confirm=discord.ui.TextInput(label='輸入「確認分潤」',max_length=6)
        self.add_item(self.cid);self.add_item(self.amount);self.add_item(self.confirm)
    async def on_submit(self,interaction):
        if str(self.confirm.value).strip()!='確認分潤':
            return await interaction.response.send_message('確認文字不正確，已取消。',ephemeral=True)
        from core.company_dividend_service import distribute
        await self.run_operation(interaction,lambda:distribute(self.guild_id,self.business_id,self.user_id,self.cid.value,self.amount.value,f'{interaction.id}'))

class TreasuryDetailView(discord.ui.View):
    def __init__(
        self,
        user_id,
        guild_id,
        business_id,
        is_owner,
        business_kind="company",
    ):
        super().__init__(timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)
        self.business_id = str(business_id)
        self.is_owner = bool(is_owner)
        self.business_kind = str(business_kind)
        from modules.company_wage_debt_ui import DebtEntry
        # DebtEntry is available only on a selected company, not on the slash-command root.
        self.gid, self.uid = self.guild_id, self.user_id
        self.add_item(DebtEntry())
        from modules.company_accounting_ui import AccountingEntry
        self.add_item(AccountingEntry())

        # 只有持有人看到管理層額度設定按鈕。
        if self.is_owner:
            self.add_item(
                OwnerSpendingLimitButton()
            )

        # The setting button already exists below. Add only the read-only
        # summary, avoiding a second button that changes the same setting.
        if self.business_kind == "company":
            from modules.company_payroll_ui import PayrollCycleSummaryButton
            self.add_item(PayrollCycleSummaryButton())

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的財務管理面板。",
                ephemeral=True,
            )
            return False

        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            self.guild_id,
            self.user_id,
        )

        allowed = {
            item["business_id"]: item
            for item in companies
        }

        if self.business_id not in allowed:
            await interaction.response.send_message(
                "你已失去此公司的財務管理權限。",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(label="🏦 銀行帳戶", style=discord.ButtonStyle.secondary, row=0)
    async def bank_details(self, interaction, button):
        await _respond(
            interaction,
            lambda: company_bank_summary(self.guild_id, self.business_id, self.user_id),
        )

    @discord.ui.button(label="📥 存入銀行", style=discord.ButtonStyle.success, row=1)
    async def bank_deposit(self, interaction, button):
        await interaction.response.send_modal(
            CompanyBankMoveModal(self.user_id, self.guild_id, self.business_id, "deposit")
        )

    @discord.ui.button(label="📤 銀行提款", style=discord.ButtonStyle.secondary, row=1)
    async def bank_withdraw(self, interaction, button):
        await interaction.response.send_modal(
            CompanyBankMoveModal(self.user_id, self.guild_id, self.business_id, "withdraw")
        )

    @discord.ui.button(label="💰 發放薪資", style=discord.ButtonStyle.success, row=1)
    async def payroll(self, interaction, button):
        await interaction.response.send_modal(
            PayrollModal(self.user_id, self.guild_id, self.business_id)
        )

    @discord.ui.button(label="📅 薪資進度", style=discord.ButtonStyle.secondary, row=2)
    async def payroll_status(self, interaction, button):
        from core.company_payroll_service import payroll_status
        await _respond(interaction, lambda: payroll_status(
            self.guild_id, self.business_id, self.user_id))

    @discord.ui.button(label="⚙️ 發薪週期", style=discord.ButtonStyle.secondary, row=2)
    async def payroll_cycle(self, interaction, button):
        if not self.is_owner or self.business_kind != "company":
            return await interaction.response.send_message("只有正式公司持有人能設定發薪週期。", ephemeral=True)
        await interaction.response.send_modal(PayrollCycleModal(
            self.user_id, self.guild_id, self.business_id))

    @discord.ui.button(label='💼 分潤準備金',style=discord.ButtonStyle.secondary,row=3)
    async def dividend_reserve(self,interaction,button):
        from core.company_profit_reserve import reserve_balance
        await _respond(interaction,lambda:f'💼 分潤準備金：{reserve_balance(self.guild_id,self.business_id,self.user_id):,} 金幣')

    @discord.ui.button(label='📥 撥入分潤',style=discord.ButtonStyle.primary,row=3)
    async def reserve_add(self,interaction,button):
        if not self.is_owner: return await interaction.response.send_message('只有持有人可撥款。',ephemeral=True)
        await interaction.response.send_modal(ReserveModal(self.user_id,self.guild_id,self.business_id))

    @discord.ui.button(label='💸 發放分潤',style=discord.ButtonStyle.success,row=3)
    async def dividend_send(self,interaction,button):
        if not self.is_owner: return await interaction.response.send_message('只有持有人可發放分潤。',ephemeral=True)
        await interaction.response.send_modal(DividendModal(self.user_id,self.guild_id,self.business_id))

    @discord.ui.button(label='📋 未發薪資',style=discord.ButtonStyle.secondary,row=3)
    async def arrears(self,interaction,button):
        from core.company_arrears import arrears_summary
        await _respond(interaction,lambda:arrears_summary(self.guild_id,self.business_id,self.user_id))

    @discord.ui.button(label="📊 財務核對", style=discord.ButtonStyle.secondary, row=1)
    async def profit(self, interaction, button):
        await _respond(
            interaction,
            lambda: profit_overview(
                self.guild_id, self.business_id, self.user_id,
            ),
        )

    @discord.ui.button(
        label="💸 資金調度",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def transfer(self, interaction, button):
        await interaction.response.send_modal(
            TreasuryTransferModal(
                self.user_id,
                self.guild_id,
                self.business_id,
            )
        )

    @discord.ui.button(
        label="🔄 更新金庫",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def refresh(self, interaction, button):
        try:
            content = await asyncio.to_thread(
                finance_summary,
                self.guild_id,
                self.business_id,
                self.user_id,
            )
        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.edit_message(
            content=content,
            view=self,
        )

    @discord.ui.button(
        label="↩️ 返回公司選擇",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(self, interaction, button):
        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            self.guild_id,
            self.user_id,
        )

        await interaction.response.edit_message(
            content=(
                f"🏦 **{organization_terms(self.guild_id)['company']}財務管理中心**\n"
                f"請先選擇{organization_terms(self.guild_id)['company']}或{organization_terms(self.guild_id)['brand']}。"
            ),
            view=TreasuryCompanyView(
                self.user_id,
                self.guild_id,
                companies,
                page=0,
            ),
        )


class OwnerSpendingLimitButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🔐 管理層額度",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction):
        view = self.view

        # 按下時重新確認持有人身分。
        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            view.guild_id,
            view.user_id,
        )

        current = next(
            (
                item for item in companies
                if item["business_id"] == view.business_id
            ),
            None,
        )

        if not current or not current["is_owner"]:
            return await interaction.response.send_message(
                "只有現任公司持有人可以設定支出上限。",
                ephemeral=True,
            )

        await interaction.response.send_modal(
            SpendingLimitModal(
                view.user_id,
                view.guild_id,
                view.business_id,
            )
        )


class TreasuryCompanySelect(discord.ui.Select):
    def __init__(self, companies):
        options = []

        for item in companies:
            options.append(
                discord.SelectOption(
                    label=item["name"][:100],
                    value=item["business_id"],
                    description=(
                        f"{item['kind']}｜"
                        f"{item['business_id']}"
                    )[:100],
                )
            )

        super().__init__(
            placeholder="選擇公司或品牌",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        parent = self.view
        business_id = self.values[0]

        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            parent.guild_id,
            parent.user_id,
        )

        selected = next(
            (
                item for item in companies
                if item["business_id"] == business_id
            ),
            None,
        )

        if not selected:
            return await interaction.response.send_message(
                "你沒有這間公司的財務權限。",
                ephemeral=True,
            )

        try:
            content = await asyncio.to_thread(
                finance_summary,
                parent.guild_id,
                business_id,
                parent.user_id,
            )
        except (
            ValueError,
            PermissionError,
            sqlite3.Error,
        ) as exc:
            return await interaction.response.send_message(
                f"⚠️ {exc}",
                ephemeral=True,
            )

        await interaction.response.edit_message(
            content=content,
            view=TreasuryDetailView(
                parent.user_id,
                parent.guild_id,
                business_id,
                selected["is_owner"],
                selected["kind"],
            ),
        )


class TreasuryPageButton(discord.ui.Button):
    def __init__(self, direction, disabled=False):
        self.direction = direction

        super().__init__(
            label=(
                "⬅️ 上一頁"
                if direction < 0
                else "下一頁 ➡️"
            ),
            style=discord.ButtonStyle.secondary,
            disabled=disabled,
            row=1,
        )

    async def callback(self, interaction):
        view = self.view

        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            view.guild_id,
            view.user_id,
        )

        page = max(
            0,
            min(
                view.page + self.direction,
                max(0, (len(companies) - 1) // 25),
            ),
        )

        await interaction.response.edit_message(
            content=(
                f"🏦 **{organization_terms(view.guild_id)['company']}財務管理中心**\n"
                f"第 {page + 1} 頁，"
                f"共 {len(companies)} 間可管理{organization_terms(view.guild_id)['company']}。"
            ),
            view=TreasuryCompanyView(
                view.user_id,
                view.guild_id,
                companies,
                page,
            ),
        )


class TreasuryCompanyView(discord.ui.View):
    def __init__(
        self,
        user_id,
        guild_id,
        companies,
        page=0,
    ):
        super().__init__(timeout=600)

        self.user_id = str(user_id)
        self.guild_id = str(guild_id)
        self.page = int(page)

        start = self.page * 25
        current = companies[start:start + 25]

        if current:
            self.add_item(
                TreasuryCompanySelect(current)
            )

        if len(companies) > 25:
            self.add_item(
                TreasuryPageButton(
                    -1,
                    disabled=self.page <= 0,
                )
            )

            self.add_item(
                TreasuryPageButton(
                    1,
                    disabled=(
                        start + 25 >= len(companies)
                    ),
                )
            )

    async def interaction_check(self, interaction):
        if (
            str(interaction.user.id) != self.user_id
            or str(interaction.guild_id) != self.guild_id
        ):
            await interaction.response.send_message(
                "這不是你的公司財務面板。",
                ephemeral=True,
            )
            return False

        return True


class CompanyTreasuryButton(discord.ui.Button):
    """供現有 /公司 面板使用。"""

    def __init__(self):
        super().__init__(
            label="🏦 金庫管理",
            style=discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction):
        companies = await asyncio.to_thread(
            manageable_finance_businesses,
            str(interaction.guild_id),
            str(interaction.user.id),
        )

        if not companies:
            return await interaction.response.send_message(
                f"目前沒有可管理財務的{organization_terms(interaction.guild_id)['company']}或{organization_terms(interaction.guild_id)['brand']}。",
                ephemeral=True,
            )

        await interaction.response.send_message(
            (
                f"🏦 **{organization_terms(interaction.guild_id)['company']}財務管理中心**\n"
                f"請選擇要操作的{organization_terms(interaction.guild_id)['company']}或{organization_terms(interaction.guild_id)['brand']}。"
            ),
            view=TreasuryCompanyView(
                str(interaction.user.id),
                str(interaction.guild_id),
                companies,
            ),
            ephemeral=True,
        )


async def setup(bot):
    initialize_treasury()
