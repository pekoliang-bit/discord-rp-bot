"""公司財務分析：區分品牌銷售統計與真正已入帳的資金。

這個模組只提供可稽核的查詢，不根據銷售統計創造金幣。
未來分潤須由已確認入帳的交易扣除成本後結算。
"""

from __future__ import annotations

import sqlite3

from core.database import account_balance, get_connection
from modules.world_terms import term
from modules.company_treasury import (
    _finance_permission,
    _treasury_account,
    initialize_treasury,
)


def profit_overview(guild_id, business_id, actor_id):
    initialize_treasury()
    gid, bid, uid = str(guild_id), str(business_id), str(actor_id)
    with get_connection() as db:
        if not _finance_permission(db, gid, bid, uid):
            raise PermissionError("你沒有這間公司的財務查閱權限。")

        bank = db.execute(
            "SELECT account_id FROM rp_company_bank_accounts "
            "WHERE guild_id=? AND company_id=?",
            (gid, bid),
        ).fetchone()
        bank_balance = (
            int(account_balance(db, gid, bank["account_id"]))
            if bank else None
        )
        treasury = int(account_balance(db, gid, _treasury_account(db, gid, bid)))

        sales = refunds = 0
        try:
            rows = db.execute("""
                SELECT kind, COALESCE(SUM(amount),0) AS total
                FROM rp_brand_sales WHERE guild_id=? AND brand_id=?
                GROUP BY kind
            """, (gid, bid)).fetchall()
            totals = {row["kind"]: int(row["total"]) for row in rows}
            sales, refunds = totals.get("sale", 0), totals.get("refund", 0)
        except sqlite3.OperationalError:
            pass

        salary_spent = 0
        try:
            row = db.execute("""
                SELECT COALESCE(SUM(amount),0) AS total
                FROM rp_company_payroll WHERE guild_id=? AND business_id=?
            """, (gid, bid)).fetchone()
            salary_spent = int(row["total"])
        except sqlite3.OperationalError:
            pass

        expense_spent = 0
        try:
            row = db.execute("""
                SELECT COALESCE(SUM(amount),0) AS total
                FROM rp_company_finance_audit
                WHERE guild_id=? AND business_id=?
                  AND action='transfer' AND amount>0
            """, (gid, bid)).fetchone()
            expense_spent = int(row["total"])
        except sqlite3.OperationalError:
            pass

        net_sales = max(0, sales - refunds)
        estimated = net_sales - salary_spent - expense_spent
        account_text = f"{bank_balance:,}" if bank_balance is not None else "尚未開戶"
        company = term(gid, "company")
        currency = term(gid, "currency")
        return (
            f"📊 **{company}財務核對｜{bid}**\n"
            f"銷售統計：{sales:,} {currency}\n"
            f"退款統計：{refunds:,} {currency}\n"
            f"淨銷售額（統計）：{net_sales:,} {currency}\n"
            f"已核發薪資：{salary_spent:,} {currency}\n"
            f"其他已標記支出：{expense_spent:,} {currency}\n"
            f"試算餘額（未完成成本歸集）：{estimated:,} {currency}\n"
            f"{company}銀行實際餘額：{account_text} {currency}\n"
            f"{company}金庫實際餘額：{treasury:,} {currency}\n\n"
            "⚠️ 銷售統計不等於銀行入帳；試算額不可直接分潤。"
            "待商城收款、退款與成本來源核對後，才會開放正式淨利潤結算。"
        )


async def setup(bot):
    """無 Slash 指令的資料服務，供 /公司 面板呼叫。"""
    initialize_treasury()
