"""排行榜獎勵資料安全驗證。"""

from modules.leaderboard_catalog import BOARD_NAMES

FORBIDDEN_VALUES = {
    "impact",
    "impact_index",
    "影響指數",
    "antis",
    "black_fans",
    "黑粉",
}

ALLOWED_VALUES = {
    "free_points",
    "skill_points",
    "heat",
    "commercial",
    "public_favor",
    "pr",
    "appearance",
    "intellect",
    "acting",
    "dance",
    "sing",
    "hosting",
    "variety",
    "talent",
    "fans",
    "awareness",
    "heat_each",
    "awareness_each",
    "business_heat",
    "group_heat",
    "shop_heat",
    "faction_honor",
    "account_fans",
    "account_traffic",
}


def validate_reward(reward):
    """驗證獎勵結構，成功時回傳 True。"""
    if not isinstance(reward, dict):
        raise ValueError("獎勵必須為字典")

    board = reward.get("board")

    if board not in BOARD_NAMES:
        raise ValueError("未知排行榜")

    if board == "awards":
        raise ValueError("第28榜尚未開放獎勵")

    rank = reward.get("rank")

    if type(rank) is not int or rank < 1:
        raise ValueError("排行榜名次錯誤")

    coins = reward.get("coins", 0)

    if type(coins) is not int or coins < 0:
        raise ValueError("金幣獎勵錯誤")

    values = reward.get("values", {})

    if not isinstance(values, dict):
        raise ValueError("數值獎勵格式錯誤")

    for name, amount in values.items():
        if name in FORBIDDEN_VALUES:
            raise ValueError(
                f"禁止直接發放管制或負面數值：{name}"
            )

        if name not in ALLOWED_VALUES:
            raise ValueError(
                f"未登記的獎勵數值：{name}"
            )

        if type(amount) is not int or amount < 0:
            raise ValueError(
                f"非法獎勵數量：{name}"
            )

    if "reward_version" not in reward or not isinstance(reward["reward_version"], str):
        raise ValueError("獎勵必須標明版本，不得匿名入帳")

    if reward.get("requires_adapter") is not True:
        raise ValueError(
            "獎勵缺少正式入帳適配器標記"
        )

    return True
