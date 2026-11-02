
"""公司員工合約與正式發薪資料核對。

只在既有 SQLite 交易中呼叫。
不扣金幣、不更改合約。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PayrollTerms:
    salary: int
    cycle_days: int
    contract_id: int | None
    source: str


def resolve_payroll_terms(
    db,
    guild_id,
    company_id,
    character_id,
    position_salary,
    company_cycle_days,
) -> PayrollTerms:
    gid = str(guild_id)
    bid = str(company_id)
    cid = int(character_id)

    if cid <= 0:
        raise ValueError("角色編號錯誤")

    cycle = int(company_cycle_days)

    if cycle not in (7, 14):
        raise ValueError("公司發薪週期必須為7或14天")

    rows = db.execute(
        """
        SELECT id, salary, cycle_days,
               employee_user_id,
               employer_accepted,
               employee_accepted
        FROM rp_company_employee_contracts
        WHERE guild_id=?
          AND company_id=?
          AND character_id=?
          AND status='active'
        """,
        (gid, bid, cid),
    ).fetchall()

    if len(rows) > 1:
        raise RuntimeError(
            "發現多份有效合約，已阻擋付款"
        )

    if not rows:
        # 尚未完成合約遷移的舊員工，
        # 暫時沿用既有職位表的薪資。
        salary = int(position_salary)

        if salary <= 0:
            raise ValueError("職位薪資必須大於零")

        return PayrollTerms(
            salary=salary,
            cycle_days=cycle,
            contract_id=None,
            source="legacy_position",
        )

    contract = rows[0]

    if (
        int(contract["employer_accepted"]) != 1
        or int(contract["employee_accepted"]) != 1
    ):
        raise RuntimeError(
            "正式合約缺少雙方確認紀錄"
        )

    contract_cycle = int(contract["cycle_days"])

    if contract_cycle != cycle:
        raise ValueError(
            f"合約規定每{contract_cycle}天發薪，"
            f"公司設定為每{cycle}天，"
            "兩者不一致，禁止扣款。"
        )

    owner = db.execute(
        """
        SELECT owner_id
        FROM characters
        WHERE guild_id=? AND id=?
          AND status='active'
        """,
        (gid, cid),
    ).fetchone()

    if (
        owner is None
        or str(owner["owner_id"])
        != str(contract["employee_user_id"])
    ):
        raise PermissionError(
            "合約員工與目前角色擁有者不一致"
        )

    salary = int(contract["salary"])

    if salary <= 0:
        raise ValueError(
            "合約薪資必須大於零，才能發薪"
        )

    return PayrollTerms(
        salary=salary,
        cycle_days=contract_cycle,
        contract_id=int(contract["id"]),
        source="active_contract",
    )
