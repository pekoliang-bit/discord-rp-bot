
"""排行榜累積與蟬聯里程碑獎勵規則。"""

THRESHOLDS = (2, 3, 5, 7, 10)

COINS = {
    "same_rank": (20000, 40000, 80000, 140000, 250000),
    "board_top3": (15000, 30000, 60000, 105000, 187500),
    "cross_top3": (10000, 20000, 40000, 70000, 125000),
    "streak": (40000, 80000, 150000, 250000, 500000),
}

POINTS = {
    "same_rank": (10, 20, 35, 50, 80),
    "board_top3": (8, 15, 26, 38, 60),
    "cross_top3": (5, 10, 18, 25, 40),
    "streak": (20, 40, 70, 100, 150),
}


def reached(kind, previous, current):
    """回傳本次新達成的門檻，不直接派發。"""
    if kind not in COINS:
        raise ValueError("未知里程碑類型")

    previous = int(previous)
    current = int(current)

    if previous < 0 or current < previous:
        raise ValueError("里程碑次數錯誤")

    result = []

    for i, threshold in enumerate(THRESHOLDS):
        if previous < threshold <= current:
            result.append({
                "kind": kind,
                "threshold": threshold,
                "coins": COINS[kind][i],
                "points_equivalent": POINTS[kind][i],
                "state": "pending_adapter",
            })

    return result
