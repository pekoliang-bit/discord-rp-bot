
"""排行榜稱號效果政策。

稱號可收藏，但正式加成須由管理者核准配置。
"""

APPROVED_EFFECTS = {}


def get_effect(board, rank):
    key = (str(board), int(rank))
    return APPROVED_EFFECTS.get(key)


def active_effects(title_rows):
    """只讀取有效且已核准的效果。"""
    result = []

    for row in title_rows:
        if not row.get("active", False):
            continue

        effect = get_effect(
            row["board"],
            row["best_rank"],
        )

        if effect is not None:
            result.append({
                "title": row["title"],
                "effect": effect,
            })

    return result


def effective_bonus(title_rows, stat):
    """同類效果只取最高值，不疊加。"""
    bonuses = []

    for item in active_effects(title_rows):
        effect = item["effect"]
        if effect.get("stat") == stat:
            bonuses.append(
                float(effect.get("value", 0))
            )

    return max(bonuses, default=0.0)
