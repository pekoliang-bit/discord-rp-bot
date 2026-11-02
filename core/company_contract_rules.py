
"""公司員工合約規則。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


VALID_PAY_CYCLES = (7, 14)
OWNER_EXIT_DAYS = 5


def utc_time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    else:
        raise ValueError("時間格式不正確")

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(timezone.utc)


def validate_contract(
    salary: int,
    cycle_days: int,
    penalty: int,
) -> None:
    if any(
        type(value) is not int
        for value in (
            salary,
            cycle_days,
            penalty,
        )
    ):
        raise ValueError("合約金額與週期必須是整數")

    if salary < 0:
        raise ValueError("薪資不可為負數")

    if cycle_days not in VALID_PAY_CYCLES:
        raise ValueError(
            "發薪週期只能是7天或14天"
        )

    if penalty < 0:
        raise ValueError("違約金不可為負數")


def self_company_deadline(founded_at):
    return utc_time(founded_at) + timedelta(
        days=OWNER_EXIT_DAYS
    )


def self_company_exit_status(
    founded_at,
    *,
    now=None,
):
    current = utc_time(
        now or datetime.now(timezone.utc)
    )

    deadline = self_company_deadline(
        founded_at
    )

    return {
        "deadline": deadline,
        "within_grace": current <= deadline,
        "overdue": current > deadline,
    }


def requires_penalty(
    penalty: int,
    *,
    mutual_release=False,
    self_company_grace=False,
):
    if penalty < 0:
        raise ValueError("違約金不可為負數")

    return (
        penalty > 0
        and not mutual_release
        and not self_company_grace
    )
