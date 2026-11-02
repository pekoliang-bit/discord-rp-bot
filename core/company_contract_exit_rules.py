
"""公司員工合約解約規則。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

GRACE_DAYS = 5

EXIT_REASONS = (
    "ordinary",
    "mutual",
    "self_company",
)


def utc_datetime(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    else:
        raise ValueError("日期格式不正確")

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(timezone.utc)


def is_self_company_grace(
    founded_at,
    hired_at,
    *,
    now=None,
):
    """必須是在任職後成立的公司，且尚在五日內。"""

    founded = utc_datetime(founded_at)
    hired = utc_datetime(hired_at)
    current = utc_datetime(
        now if now is not None
        else datetime.now(timezone.utc)
    )

    if founded < hired or founded > current:
        return False

    return current <= founded + timedelta(
        days=GRACE_DAYS
    )


def calculate_exit_penalty(
    contractual_penalty,
    reason,
    *,
    mutual_approved=False,
    self_company_verified=False,
):
    if type(contractual_penalty) is not int:
        raise ValueError("違約金必須是整數")

    if contractual_penalty < 0:
        raise ValueError("違約金不可為負數")

    if reason not in EXIT_REASONS:
        raise ValueError("未知的解約類型")

    if reason == "mutual":
        if not mutual_approved:
            raise PermissionError(
                "雙方協議解約尚未取得公司同意"
            )
        return 0

    if reason == "self_company":
        if not self_company_verified:
            raise PermissionError(
                "未符合五日內合理解約資格"
            )
        return 0

    return contractual_penalty
