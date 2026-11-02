"""排行榜累積及連續蟬聯獎勵：只計算待派發明細。"""
from decimal import Decimal, ROUND_HALF_UP

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
    """僅當本期首次跨越門檻時產生獎勵。"""
    if kind not in COINS:
        raise ValueError("未知里程碑類型")
    if previous < 0 or current < previous:
        raise ValueError("里程碑次數錯誤")
    return [
        {"kind": kind, "threshold": t, "coins": COINS[kind][i],
         "points_equivalent": POINTS[kind][i], "state": "pending_adapter"}
        for i, t in enumerate(THRESHOLDS) if previous < t <= current
    ]
