
"""品牌熱度來源及35%上限計算。

消費：每1000金幣 = 1點熱度。
四種來源各最多計入目標熱度的35%。
"""

from __future__ import annotations

SOURCES = (
    "consumption",
    "promotion",
    "cooperation",
    "representative",
)


def calculate_brand_heat(
    target_heat: int,
    net_consumption: int,
    promotion: int = 0,
    cooperation: int = 0,
    representative: int = 0,
) -> dict:
    target_heat = int(target_heat)
    net_consumption = int(net_consumption)

    if target_heat <= 0:
        raise ValueError("目標熱度必須大於零")

    if net_consumption < 0:
        raise ValueError("淨消費不可為負數")

    values = {
        "consumption": net_consumption // 1000,
        "promotion": int(promotion),
        "cooperation": int(cooperation),
        "representative": int(representative),
    }

    if any(value < 0 for value in values.values()):
        raise ValueError("熱度來源不可為負數")

    limit = target_heat * 35 // 100

    effective = {
        name: min(value, limit)
        for name, value in values.items()
    }

    return {
        "raw": values,
        "effective": effective,
        "source_limit": limit,
        "total": sum(effective.values()),
        "target": target_heat,
        "qualified": (
            sum(effective.values()) >= target_heat
        ),
    }


def heat_source_breakdown(data: dict) -> str:
    names = {
        "consumption": "消費",
        "promotion": "宣傳",
        "cooperation": "合作",
        "representative": "代表曝光",
    }

    lines = [
        "📊 品牌熱度來源",
        f"目標熱度：{data['target']:,}",
        f"單一來源上限：{data['source_limit']:,}",
    ]

    for source in SOURCES:
        raw = data["raw"][source]
        effective = data["effective"][source]

        lines.append(
            f"{names[source]}："
            f"{effective:,} / {raw:,}"
        )

    lines.append(
        f"有效總熱度：{data['total']:,}"
    )

    lines.append(
        "升級熱度達標："
        + ("是" if data["qualified"] else "否")
    )

    return "\n".join(lines)
