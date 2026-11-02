
"""排行榜基礎獎勵規則。

- 14 天小結算不呼叫本模組發獎。
- 30 天大結算計算有效入榜者的獎勵。
- 第 28 榜 awards 暫緩。
- 僅產生獎勵資料，不直接修改金幣或屬性。
- 150 點上限只適用於創角，不在此限制成長。
"""

from __future__ import annotations

from modules.leaderboard_catalog import BOARD_NAMES

TOP_POINTS = (100, 80, 65, 55, 50, 45, 40, 35, 30, 25)

# 各榜前十名獨立獎金。
TOP_COINS = {
    "impact": (150000, 100000, 70000, 50000, 40000,
               30000, 25000, 20000, 15000, 10000),
    "wealth": (120000, 80000, 55000, 40000, 30000,
               25000, 20000, 15000, 10000, 5000),
    "heat": (100000, 70000, 50000, 35000, 25000,
             20000, 15000, 10000, 7500, 5000),
    "commercial": (110000, 75000, 55000, 40000, 30000,
                   22000, 16000, 12000, 8000, 5000),
    "public_favor": (90000, 60000, 45000, 30000, 22000,
                     17000, 13000, 10000, 7000, 4000),
    "pr": (95000, 65000, 45000, 32000, 24000,
           18000, 14000, 10000, 7000, 4000),
    "appearance": (100000, 70000, 50000, 35000, 25000,
                   20000, 15000, 10000, 7500, 5000),
    "intellect": (105000, 72000, 52000, 37000, 27000,
                  20000, 15000, 11000, 8000, 5000),
    "main_skill": (140000, 95000, 70000, 52000, 40000,
                   32000, 26000, 20000, 15000, 10000),
    "acting": (160000, 110000, 80000, 60000, 45000,
               35000, 28000, 22000, 16000, 12000),
    "dance": (125000, 85000, 62000, 47000, 36000,
              28000, 22000, 17000, 13000, 9000),
    "sing": (150000, 105000, 75000, 56000, 43000,
             34000, 27000, 21000, 16000, 11000),
    "hosting": (115000, 78000, 57000, 43000, 33000,
                26000, 21000, 16000, 12000, 8000),
    "variety": (135000, 92000, 68000, 50000, 39000,
                31000, 25000, 19000, 14000, 10000),
    "talent": (120000, 82000, 60000, 45000, 35000,
               27000, 22000, 17000, 12500, 8500),
    "fans": (175000, 120000, 88000, 65000, 50000,
             39000, 31000, 24000, 18000, 13000),
    "antis": (120000, 85000, 60000, 45000, 35000,
              27000, 20000, 15000, 11000, 8000),
    "awareness": (145000, 100000, 72000, 54000, 41000,
                  32000, 25000, 19000, 14000, 10000),
    "cp": (160000, 110000, 80000, 60000, 45000,
           35000, 27000, 21000, 15000, 11000),
    "works": (150000, 105000, 76000, 57000, 43000,
              34000, 27000, 21000, 16000, 11000),
    "endorsements": (155000, 108000, 78000, 58000, 44000,
                     35000, 28000, 22000, 16500, 11500),
    "industry": (220000, 155000, 110000, 82000, 62000,
                 48000, 38000, 29000, 22000, 15000),
    "groups": (300000, 210000, 150000, 110000, 85000,
               65000, 50000, 38000, 28000, 20000),
    "shops": (130000, 90000, 65000, 48000, 37000,
              29000, 23000, 18000, 13500, 9000),
    "factions": (300000, 220000, 160000, 120000, 90000,
                 70000, 55000, 42000, 30000, 20000),
    "social": (180000, 125000, 90000, 68000, 52000,
               40000, 32000, 25000, 19000, 14000),
    "newcomer": (200000, 140000, 100000, 75000, 57000,
                 44000, 35000, 27000, 20000, 15000),
}

CORE_TYPES = {
    "impact": "free_points",
    "wealth": "coins",
    "heat": "heat",
    "commercial": "commercial",
    "public_favor": "public_favor",
    "pr": "pr",
    "appearance": "appearance",
    "intellect": "intellect",
    "main_skill": "skill_points",
    "acting": "acting",
    "dance": "dance",
    "sing": "sing",
    "hosting": "hosting",
    "variety": "variety",
    "talent": "talent",
    "fans": "fans",
    "antis": "heat",
    "awareness": "awareness",
    "cp": "heat",
    "works": "skill_points",
    "endorsements": "commercial",
    "industry": "business_heat",
    "groups": "group_heat",
    "shops": "shop_heat",
    "factions": "faction_honor",
    "social": "account_fans",
    "newcomer": "free_points",
}

# 已有專屬數值提案的排行榜。
SPECIAL_TOP = {
    "antis": {
        "heat": (15000, 12000, 10000, 8000, 6500,
                 5500, 4500, 3500, 3000, 2500),
        "awareness": TOP_POINTS,
    },
    "awareness": {
        "awareness": (150, 120, 100, 85, 75,
                      65, 55, 45, 35, 30),
        "heat": (8000, 6000, 4500, 3500, 3000,
                 2500, 2000, 1500, 1000, 800),
    },
    "industry": {
        "business_heat": (10000, 8000, 6500, 5500, 5000,
                          4500, 4000, 3500, 3000, 2500),
    },
    "groups": {
        "group_heat": (20000, 16000, 13000, 11000, 10000,
                       9000, 8000, 7000, 6000, 5000),
    },
    "shops": {
        "shop_heat": (8000, 6500, 5000, 4000, 3500,
                      3000, 2500, 2000, 1500, 1000),
    },
    "factions": {
        "faction_honor": (10000, 8000, 6500, 5500, 5000,
                          4500, 4000, 3500, 3000, 2500),
    },
    "social": {
        "account_fans": (100000, 80000, 65000, 55000, 50000,
                         45000, 40000, 35000, 30000, 25000),
        "account_traffic": (15000, 12000, 10000, 8000, 7000,
                            6000, 5000, 4000, 3000, 2500),
    },
    "newcomer": {
        "free_points": TOP_POINTS,
        "heat": (10000, 8000, 6500, 5500, 5000,
                 4500, 4000, 3500, 3000, 2500),
    },
}

# CP 榜的數值為每名成員取得的數量。
SPECIAL_TOP["cp"] = {
    "heat_each": (5000, 4000, 3000, 2500, 2000,
                  1500, 1200, 1000, 750, 500),
    "awareness_each": TOP_POINTS,
}


def _bracket(rank):
    if rank <= 20:
        return 15, 5000
    if rank <= 25:
        return 10, 4000
    if rank <= 40:
        return 10, 3000
    if rank <= 55:
        return 5, 2000
    return 5, 1000


def calculate(board, rank):
    """回傳待派發獎勵資料；None 代表尚未配置。

    所有資料只用於獎勵帳本。
    實際入帳須由經過驗證的適配器處理。
    """
    board = str(board)
    rank = int(rank)

    if rank < 1:
        raise ValueError("排行榜名次必須大於零")

    if board == "awards":
        return None

    if board not in BOARD_NAMES:
        return None

    if board not in TOP_COINS:
        return None

    core_type = CORE_TYPES[board]

    if rank <= 10:
        coins = TOP_COINS[board][rank - 1]
        points = TOP_POINTS[rank - 1]
    else:
        points, coins = _bracket(rank)

    values = {}

    if rank <= 10 and board in SPECIAL_TOP:
        for key, amounts in SPECIAL_TOP[board].items():
            values[key] = amounts[rank - 1]
    else:
        values[core_type] = points

    # 金錢榜的核心獎勵就是金幣，
    # 不另外產生一筆重複的核心金幣。
    if board == "wealth":
        values.pop("coins", None)

    # 黑紅榜只發放正面數值。
    if board == "antis":
        values.pop("antis", None)
        if rank > 10:
            values = {
                "heat": points * 100,
                "awareness": points,
            }

    # CP 獎金為組合總額，不在此直接拆帳。
    # 實際發放時必須驗證雙方身份。
    if board == "cp":
        values.pop("heat", None)
        if rank > 10:
            values = {
                "heat_each": points * 100,
                "awareness_each": points,
            }

    # 社群帳號的粉絲與流量歸屬帳號。
    if board == "social" and rank > 10:
        values = {
            "account_fans": points * 1000,
            "account_traffic": points * 100,
        }

    # 組織熱度與陣營榮譽使用較大單位。
    if rank > 10 and board in (
        "industry", "groups", "shops", "factions"
    ):
        values = {core_type: points * 100}

    # 所有結果都只建立待派發紀錄。
    # 正式入帳前必須確認對應獎勵單位、
    # 角色身份、組織金庫與重複領獎保護。
    return {
        "board": board,
        "rank": rank,
        "coins": coins,
        "values": values,
        "configured": True,
        "requires_adapter": True,
        "reward_version": "v11-provisional",
    }
