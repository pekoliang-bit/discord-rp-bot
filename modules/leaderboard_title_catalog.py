
"""排行榜前三名稱號名稱。"""

from modules.leaderboard_catalog import BOARD_NAMES

RANK_PREFIX = {
    1: "巔峰",
    2: "卓越",
    3: "菁英",
}

TITLE_SUFFIX = {
    "impact": "巨星",
    "wealth": "財富領袖",
    "heat": "話題人物",
    "commercial": "商業寵兒",
    "public_favor": "國民之星",
    "pr": "公關大師",
    "appearance": "顏值代表",
    "intellect": "才智之星",
    "main_skill": "實力王牌",
    "acting": "銀幕之星",
    "dance": "舞台舞者",
    "sing": "樂壇歌者",
    "hosting": "金牌主持",
    "variety": "綜藝之星",
    "talent": "全能藝人",
    "fans": "人氣之星",
    "antis": "話題焦點",
    "awareness": "全民焦點",
    "cp": "螢幕搭檔",
    "works": "作品之星",
    "endorsements": "品牌寵兒",
    "industry": "產業領袖",
    "groups": "集團領袖",
    "shops": "商業新星",
    "factions": "陣營先鋒",
    "social": "網路紅人",
    "newcomer": "年度新星",
}


def title_for(board, rank):
    """第28榜及非前三名不產生稱號。"""
    board = str(board)
    rank = int(rank)

    if board == "awards":
        return None

    if board not in BOARD_NAMES:
        raise ValueError("未知排行榜")

    if rank not in RANK_PREFIX:
        return None

    suffix = TITLE_SUFFIX.get(board)

    if suffix is None:
        return None

    return f"{RANK_PREFIX[rank]}{suffix}"


def title_metadata(board, rank):
    title = title_for(board, rank)

    if title is None:
        return None

    return {
        "board": board,
        "rank": int(rank),
        "title": title,
        "collectible": True,
        "buff_configured": False,
    }
