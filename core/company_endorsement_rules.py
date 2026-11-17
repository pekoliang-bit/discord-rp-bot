
"""品牌合作與代言 T1～T10 規則。"""

from __future__ import annotations

TIERS = {
    1: "產品體驗官",  # T1～T3 暫定稱呼；可於此統一調整
    2: "品牌推薦官",
    3: "品牌摯友",
    4: "品牌大使",
    5: "品牌繆思",
    6: "形象／系列代言人",
    7: "國家／地區代言人",
    8: "洲區代言人",
    9: "全球代言人",
    10: "品牌起源代言人",
}

MARKETS = {
    1: "experience",
    2: "recommendation",
    3: "friend",
    4: "brand",
    5: "muse",
    6: "series",
    7: "country",
    8: "region",
    9: "global",
    10: "origin",
}

HIGH_TIER_COMPETITOR = frozenset({8, 9, 10})
LOW_TIER_RISK = frozenset({4, 5})


def validate_endorsement(tier, scope):
    if type(tier) is not int or tier not in TIERS:
        raise ValueError("代言等級只能是T1～T10")

    scope = str(scope).strip()
    if not scope:
        raise ValueError("必須填寫代言範圍")

    if len(scope) > 120:
        raise ValueError("代言範圍過長")

    if tier in (1, 2, 3, 4, 5, 9, 10):
        scope = MARKETS[tier]

    return scope


def displayed_endorsement(tier, scope):
    tier = int(tier)
    validate_endorsement(tier, scope)

    if tier in (6, 7, 8):
        return f"T{tier} {TIERS[tier]}｜{scope}"

    return f"T{tier} {TIERS[tier]}"


def competitor_risk(
    existing_tiers,
    new_tier,
    *,
    competing_brand=False,
):
    """回傳風險標記，不擅自給予隨機數值。"""
    new_tier = int(new_tier)

    if not competing_brand:
        return {
            "competitor": False,
            "risk_reduction_percent": 0,
            "low_title_dispute": False,
        }

    highest = max(
        [int(t) for t in existing_tiers],
        default=0,
    )

    return {
        "competitor": True,
        "risk_reduction_percent": (
            60 if highest in HIGH_TIER_COMPETITOR else 0
        ),
        "low_title_dispute": (
            highest >= 8
            and new_tier in LOW_TIER_RISK
        ),
    }
