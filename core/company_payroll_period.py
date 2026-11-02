"""公司員工 7／14 天發薪週期計算。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ALLOWED_CYCLES = (7, 14)


@dataclass(frozen=True)
class PayrollPeriod:
    key: str
    start: datetime
    end: datetime
    cycle_days: int


def parse_time(value) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    else:
        raise ValueError("無效的入職時間")

    # SQLite CURRENT_TIMESTAMP 使用 UTC。
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


def payroll_period(
    hired_at,
    cycle_days: int,
    *,
    now=None,
) -> PayrollPeriod | None:
    """取得目前最新已完成的薪資期。

    未滿第一個週期時回傳 None。
    """
    if type(cycle_days) is not int:
        raise ValueError("發薪週期必須是整數")

    if cycle_days not in ALLOWED_CYCLES:
        raise ValueError("只能設定7天或14天")

    hired = parse_time(hired_at)
    current = parse_time(
        now if now is not None
        else datetime.now(timezone.utc)
    )

    if current < hired:
        raise ValueError("入職時間不能晚於目前時間")

    cycle = timedelta(days=cycle_days)
    completed = (current - hired) // cycle

    if completed < 1:
        return None

    end = hired + completed * cycle
    start = end - cycle

    # 納入入職錨點與週期編號，避免不同任期撞鍵。
    anchor = hired.strftime("%Y%m%dT%H%M%S%fZ")
    key = f"{cycle_days}d:{anchor}:{completed}"

    return PayrollPeriod(
        key=key,
        start=start,
        end=end,
        cycle_days=cycle_days,
    )


def payday_text(period: PayrollPeriod) -> str:
    return (
        f"{period.cycle_days}天制｜"
        f"{period.start:%Y-%m-%d} 至 "
        f"{period.end:%Y-%m-%d}"
    )
