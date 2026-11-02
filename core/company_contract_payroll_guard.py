
"""員工合約生效前的發薪週期防護。"""

from __future__ import annotations


def validate_contract_activation(
    db,
    guild_id,
    company_id,
    contract_cycle_days,
):
    gid = str(guild_id)
    bid = str(company_id)
    days = int(contract_cycle_days)

    if days not in (7, 14):
        raise ValueError(
            "合約發薪週期必須為7或14天"
        )

    row = db.execute(
        """
        SELECT cycle_days
        FROM rp_company_payroll_settings
        WHERE guild_id=?
          AND business_id=?
        """,
        (gid, bid),
    ).fetchone()

    if row is None:
        raise ValueError(
            "公司尚未設定正式薪資週期，"
            "不能啟用員工合約"
        )

    company_days = int(row["cycle_days"])

    if company_days != days:
        raise ValueError(
            f"公司薪資週期為{company_days}天，"
            f"合約卻是{days}天。"
            "請公司持有人先統一設定。"
        )

    return True
