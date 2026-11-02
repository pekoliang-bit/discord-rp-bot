
"""排行榜點數分配的合法目標。"""

from modules.leaderboard_catalog import (
    ATTRIBUTE_ALIASES,
    SKILL_ALIASES,
)

FORBIDDEN = {
    "影響指數",
    "impact",
    "impact_index",
    "黑粉",
    "black_fans",
}

ATTRIBUTE_NAMES = frozenset(
    names[0]
    for names in ATTRIBUTE_ALIASES.values()
    if names
)

SKILL_NAMES = frozenset(
    names[0]
    for names in SKILL_ALIASES.values()
    if names
)


def available_targets(pool):
    """回傳指定點數池允許的目標。"""
    if pool == "free":
        return {
            "attributes": sorted(ATTRIBUTE_NAMES),
            "skills": sorted(SKILL_NAMES),
        }

    if pool == "skill":
        return {
            "attributes": [],
            "skills": sorted(SKILL_NAMES),
        }

    raise ValueError("未知點數池")


def validate_target(pool, category, name):
    """確認點數種類及分配目標合法。"""
    name = str(name).strip()

    if name in FORBIDDEN:
        raise ValueError("禁止分配影響指數等管制數值")

    targets = available_targets(pool)

    if category == "attribute":
        if name not in targets["attributes"]:
            raise ValueError("未開放的屬性")
        return True

    if category == "skill":
        if name not in targets["skills"]:
            raise ValueError("未開放的技能")
        return True

    raise ValueError("目標分類錯誤")
