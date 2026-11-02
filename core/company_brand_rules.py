"""品牌升級正式規則。

不連線資料庫，不直接更動品牌等級。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

LEVELS = (
    "平價",
    "小資",
    "輕奢",
    "奢侈",
    "高奢",
)

REQUIREMENTS = {
    "小資": (10_000, 200_000),
    "輕奢": (20_000, 1_000_000),
    "奢侈": (100_000, 5_000_000),
    "高奢": (1_000_000, 25_000_000),
}

DESIGN_WORDS = (
    "設計", "服裝", "服飾", "珠寶",
    "飾品", "皮具", "皮革", "鞋履",
    "鞋類", "家具", "家居設計",
    "香水", "美妝", "彩妝",
)

RESTAURANT_WORDS = (
    "餐廳", "餐飲", "料理", "咖啡廳",
    "甜點店", "食堂",
)

WAIT_DAYS = 14
WINDOW_DAYS = 30
SOURCE_CAP_PERCENT = 35


def next_brand_level(current: str) -> str | None:
    if current not in LEVELS:
        raise ValueError("未知的品牌等級")

    index = LEVELS.index(current)

    if index == len(LEVELS) - 1:
        return None

    return LEVELS[index + 1]


def allowed_brand_level(
    industry: str,
    target: str,
) -> bool:
    if target not in LEVELS:
        raise ValueError("未知的品牌等級")

    industry = str(industry or "").strip()

    if target != "高奢":
        return True

    if any(
        word in industry
        for word in RESTAURANT_WORDS
    ):
        return False

    return any(
        word in industry
        for word in DESIGN_WORDS
    )


def normalize_datetime(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        parsed = datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )

    else:
        raise ValueError("品牌日期格式不正確")

    if parsed.tzinfo is None:
        # 舊資料庫的 naive 時間按 UTC 解讀。
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def enough_age(
    founded_at,
    *,
    now: datetime | None = None,
) -> bool:
    founded = normalize_datetime(founded_at)

    current = normalize_datetime(
        now if now is not None
        else datetime.now(timezone.utc)
    )

    return current - founded >= timedelta(
        days=WAIT_DAYS
    )


def evaluate_brand_upgrade(
    current_level: str,
    industry: str,
    founded_at,
    heat: int,
    net_revenue: int,
    *,
    now: datetime | None = None,
) -> dict:
    """回傳下一級升級的資格與未通過原因。

    角色屬性、品牌代表等其他既有條件，
    仍由原本的 company_brand_progress 檢查。
    """

    target = next_brand_level(current_level)

    if target is None:
        return {
            "eligible": False,
            "target": None,
            "reasons": ("已達最高等級",),
        }

    required_heat, required_revenue = (
        REQUIREMENTS[target]
    )

    reasons = []

    if not enough_age(founded_at, now=now):
        reasons.append("成立未滿14天")

    if not allowed_brand_level(
        industry, target
    ):
        reasons.append("產業不符合升級資格")

    if int(heat) < required_heat:
        reasons.append("品牌熱度不足")

    if int(net_revenue) < required_revenue:
        reasons.append("30天淨營業額不足")

    return {
        "eligible": not reasons,
        "target": target,
        "required_heat": required_heat,
        "required_revenue": required_revenue,
        "reasons": tuple(reasons),
    }
