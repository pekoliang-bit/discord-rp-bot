
"""已核准的路人角色規則；48 小時成長與初始化共用。"""
from __future__ import annotations
import random

INITIAL_MAX_LEVEL = 9
GROWTH_HOURS = 48
CATCHUP_LIMIT = 7
ABILITY_GROWTH_CHANCE = .75

EXPERIENCE_POOLS = (
    (0, 0, 500, 600, 500, 650),
    (1, 3, 550, 700, 600, 800),
    (4, 7, 600, 850, 750, 1000),
    (8, 12, 700, 950, 900, 1150),
    (13, 19, 800, 1100, 1000, 1300),
    (20, 200, 900, 1200, 1100, 1400),
)

RESOURCE_BANDS = (
    (1, 4, (0, 3000), (0, 300), (0, 500),
     (0, 1000), (1000, 30000)),
    (5, 8, (3000, 30000), (300, 2000), (500, 5000),
     (1000, 10000), (30000, 150000)),
    (9, 9, (30000, 300000), (2000, 10000),
     (5000, 30000), (10000, 50000), (150000, 500000)),
)

TYPE_CHANCES = {
    '演員': (50, 30, 20),
    '歌手': (50, 25, 25),
    '舞者': (60, 25, 15),
    '偶像': (25, 25, 50),
    '模特兒': (45, 35, 20),
    '配音員': (60, 25, 15),
    '主持人': (30, 35, 35),
    '綜藝藝人': (30, 30, 40),
    '網紅': (30, 30, 40),
    '導演': (45, 35, 20),
    '編劇': (60, 30, 10),
    '編舞師': (55, 30, 15),
    '製作人': (30, 50, 20),
    '經紀人': (35, 50, 15),
}

SPLITS = {
    'specialist': (.55, .25, .20),
    'balanced': (.25, .25, .50),
    'versatile': (.35, .45, .20),
}

CAFE_BIAS = (
    (1, 4, .0, .55),
    (5, 8, .15, .70),
    (9, 9, .30, .85),
)

def sample_pool(years, level, rng=None):
    rng = rng or random
    band = next(
        (r for r in EXPERIENCE_POOLS if r[0] <= years <= r[1]),
        EXPERIENCE_POOLS[-1]
    )
    lo, hi = next(
        (r[2], r[3]) for r in CAFE_BIAS
        if r[0] <= level <= r[1]
    )
    ratio = rng.uniform(lo, hi)
    return (
        round(band[2] + (band[3] - band[2]) * ratio),
        round(band[4] + (band[5] - band[4]) * ratio),
    )

def sample_resources(level, rng=None):
    rng = rng or random
    band = next(
        r for r in RESOURCE_BANDS
        if r[0] <= level <= r[1]
    )
    return dict(zip(
        ('fans', 'awareness', 'heat', 'commercial', 'coins'),
        (rng.randint(*range_pair) for range_pair in band[2:])
    ))

def choose_type(job, rng=None):
    rng = rng or random
    weights = TYPE_CHANCES.get(job, (45, 30, 25))
    return rng.choices(
        ('specialist', 'balanced', 'versatile'),
        weights=weights,
        k=1
    )[0]
