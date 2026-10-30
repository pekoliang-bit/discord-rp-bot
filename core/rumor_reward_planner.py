
"""傳聞獎懲方案產生器（純計算，不寫資料庫）。

安裝：core/rumor_reward_planner.py

本檔不是 discord.py extension，
不得由 main.py 當作 Discord Cog 自動載入。

官方需先審核設定值。
本模組不直接發放金幣、稱號或影響指數。
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from core.stat_rewards import ATTRIBUTES, METRICS


GRADES = frozenset("SABCD")

# 僅包含現有數值欄位。
# 排除需要另外結算的特殊管制項目。
ALLOWED = (
    ATTRIBUTES | METRICS
) - {
    "影響指數",
    "CP好感度",
}


@dataclass(frozen=True)
class RuleConfig:
    """官方審核後的單一規則版本設定。

    所有欄位均需明確提供。
    """

    version: str

    # grade -> {
    #     stat_name: signed integer coefficient
    # }
    grade_changes: Mapping[
        str,
        Mapping[str, int],
    ]

    # T1～T5 對應整數百分比。
    # 例如：T1 = 100
    tier_percent: Mapping[int, int]

    # 各屬性單次結算絕對上限。
    caps: Mapping[str, int]

    # 熱度加成基準。
    heat_reference: int

    # 熱度加成上限，千分比。
    max_heat_bonus_permille: int


@dataclass(frozen=True)
class RewardPlan:
    """可追溯的傳聞獎懲計算結果。"""

    guild_id: str
    rumor_id: int
    character_id: int
    grade: str
    tier: int
    heat: int
    version: str
    changes: dict[str, int]
    fingerprint: str


def _integer(
    value: object,
    label: str,
    low: int,
    high: int,
) -> int:
    """驗證整數範圍，拒絕布林值。"""

    if (
        type(value) is not int
        or not low <= value <= high
    ):
        raise ValueError(
            f"{label} 必須是 "
            f"{low}～{high} 的整數"
        )

    return value


def _validate_rules(
    rules: RuleConfig,
) -> None:
    """驗證官方獎懲規則。"""

    if (
        not isinstance(rules.version, str)
        or not rules.version.strip()
    ):
        raise ValueError(
            "規則版本不可為空"
        )

    if set(rules.grade_changes) != GRADES:
        raise ValueError(
            "須完整設定 S、A、B、C、D "
            "五個評級"
        )

    if set(rules.tier_percent) != {
        1, 2, 3, 4, 5
    }:
        raise ValueError(
            "須完整設定 T1～T5 倍率"
        )

    for tier, percent in (
        rules.tier_percent.items()
    ):
        _integer(
            percent,
            f"T{tier} 倍率",
            0,
            10000,
        )

    _integer(
        rules.heat_reference,
        "熱度基準",
        1,
        10**12,
    )

    _integer(
        rules.max_heat_bonus_permille,
        "熱度加成上限",
        0,
        10000,
    )

    if not rules.caps:
        raise ValueError(
            "官方必須設定數值上限"
        )

    for stat, cap in rules.caps.items():

        if stat not in ALLOWED:
            raise ValueError(
                f"不支援的獎懲欄位：{stat}"
            )

        _integer(
            cap,
            f"{stat} 上限",
            0,
            10**12,
        )

    for grade, change_map in (
        rules.grade_changes.items()
    ):
        for stat, value in (
            change_map.items()
        ):

            if (
                stat not in ALLOWED
                or stat not in rules.caps
            ):
                raise ValueError(
                    f"{grade} 使用未核准的數值："
                    f"{stat}"
                )

            _integer(
                value,
                f"{grade}/{stat} 係數",
                -(10**9),
                10**9,
            )


def make_reward_plan(
    guild_id: str | int,
    rumor_id: int,
    character_id: int,
    grade: str,
    tier: int,
    heat: int,
    rules: RuleConfig,
) -> RewardPlan:
    """根據案件等級與官方規則產生獎懲方案。

    注意：
    本函式不驗證案件是否真實成立、
    審核權限或角色所有權。

    這些條件必須由可信任的案件結算器
    查詢資料庫後確認。

    本函式僅計算，不寫入資料庫。
    """

    _validate_rules(rules)

    gid = str(guild_id).strip()

    if not gid:
        raise ValueError(
            "guild_id 不可為空"
        )

    rid = _integer(
        rumor_id,
        "案件編號",
        1,
        10**15,
    )

    cid = _integer(
        character_id,
        "角色編號",
        1,
        10**15,
    )

    level = str(grade).upper().strip()

    if level not in GRADES:
        raise ValueError(
            "無效的公關評級"
        )

    tier = _integer(
        tier,
        "案件等級",
        1,
        5,
    )

    heat = _integer(
        heat,
        "案件熱度",
        0,
        10**12,
    )

    bonus = min(
        rules.max_heat_bonus_permille,
        (
            heat
            * rules.max_heat_bonus_permille
        ) // rules.heat_reference,
    )

    multiplier = (
        Decimal(
            rules.tier_percent[tier]
        ) / Decimal(100)
        * (
            Decimal(1000 + bonus)
            / Decimal(1000)
        )
    )

    changes: dict[str, int] = {}

    for stat, base in sorted(
        rules.grade_changes[level].items()
    ):

        amount = int(
            (
                Decimal(abs(base))
                * multiplier
            ).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

        amount = min(
            amount,
            rules.caps[stat],
        )

        if amount:
            changes[stat] = (
                amount
                if base > 0
                else -amount
            )

    canonical = {
        "guild_id": gid,
        "rumor_id": rid,
        "character_id": cid,
        "grade": level,
        "tier": tier,
        "heat": heat,
        "version": rules.version,
        "changes": changes,
    }

    fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return RewardPlan(
        guild_id=gid,
        rumor_id=rid,
        character_id=cid,
        grade=level,
        tier=tier,
        heat=heat,
        version=rules.version,
        changes=changes,
        fingerprint=fingerprint,
    )
