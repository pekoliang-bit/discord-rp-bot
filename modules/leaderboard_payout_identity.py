
"""排行榜派發身分安全檢查。"""

from modules.leaderboard_entity_identity import parse_identity

BOARD_TYPES = {
    "industry": {"company"},
    "groups": {"group"},
    "shops": {"shop"},
    "factions": {"faction"},
    "cp": {"cp"},
    "social": {"social_account"},
}

CHARACTER_BOARDS = {
    "impact", "wealth", "heat", "commercial",
    "public_favor", "pr", "appearance", "intellect",
    "main_skill", "acting", "dance", "sing",
    "hosting", "variety", "talent", "fans",
    "antis", "awareness", "works",
    "endorsements", "newcomer",
}


def validate_identity(board, entity_key):
    board = str(board)

    if board == "awards":
        raise ValueError("第28榜尚未開放獎勵")

    parsed = parse_identity(entity_key)
    if parsed is None:
        raise ValueError(
            "舊式或無類型身分不可直接入帳"
        )

    if board in CHARACTER_BOARDS:
        allowed = {"character"}
    else:
        allowed = BOARD_TYPES.get(board)

    if not allowed:
        raise ValueError("未知排行榜類型")

    if parsed["entity_type"] not in allowed:
        raise ValueError(
            f"{board} 不接受 "
            f"{parsed['entity_type']} 身分"
        )

    return parsed
