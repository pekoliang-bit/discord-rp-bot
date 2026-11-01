
"""公司銀行帳戶資料一致性核對。

唯讀稽核服務，不修正餘額。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.database import (
    get_connection,
    account_balance,
)


@dataclass(frozen=True)
class CompanyBankAudit:
    company_id: str
    account_id: int
    status: str
    balance: int
    healthy: bool
    issues: tuple[str, ...]


def audit_company_bank(
    guild_id,
    company_id,
    *,
    trusted=False,
) -> CompanyBankAudit:
    if trusted is not True:
        raise PermissionError(
            "只有受授權的系統服務可以執行稽核。"
        )

    gid = str(guild_id).strip()
    bid = str(company_id).strip()

    if not gid or not bid:
        raise ValueError("缺少伺服器或公司編號。")

    with get_connection() as db:
        record = db.execute("""
            SELECT
                account_id,
                status,
                initial_deposit
            FROM rp_company_bank_accounts
            WHERE guild_id=? AND company_id=?
        """, (gid, bid)).fetchone()

        if record is None:
            raise LookupError(
                "該公司尚未正式開戶。"
            )

        account_id = int(record["account_id"])
        status = str(record["status"])
        issues = []

        account = db.execute("""
            SELECT
                owner_type,
                owner_id,
                account_type
            FROM accounts
            WHERE guild_id=? AND id=?
        """, (gid, account_id)).fetchone()

        if account is None:
            issues.append("找不到對應的帳本帳戶")
        else:
            if account["owner_type"] != "organization":
                issues.append("帳戶持有人類型錯誤")

            if str(account["owner_id"]) != bid:
                issues.append("帳戶所屬公司錯誤")

            if account["account_type"] != "bank":
                issues.append("帳戶類型不是公司銀行帳戶")

        company = db.execute("""
            SELECT status, kind
            FROM rp_business_entities
            WHERE guild_id=? AND business_id=?
        """, (gid, bid)).fetchone()

        if company is None:
            issues.append("找不到公司主資料")
        elif company["kind"] != "company":
            issues.append("銀行帳戶並非登記給公司")
        elif company["status"] != "active":
            issues.append("公司並非啟用狀態")

        if status not in ("active", "frozen"):
            issues.append("銀行帳戶狀態錯誤")

        if int(record["initial_deposit"]) < 0:
            issues.append("開戶首存資料不合法")

        balance = (
            int(account_balance(db, gid, account_id))
            if account is not None
            else 0
        )

    return CompanyBankAudit(
        company_id=bid,
        account_id=account_id,
        status=status,
        balance=balance,
        healthy=not issues,
        issues=tuple(issues),
    )
