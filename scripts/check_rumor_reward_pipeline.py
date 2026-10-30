
"""Discord RP 傳聞獎懲模組安全檢查。

執行：
    python -m scripts.check_rumor_reward_pipeline

使用臨時測試規則，不修改正式規則。
不寫入資料庫、不發放獎勵、
不變更玩家或角色資料。
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)

    print(f"[OK] {message}")


def expect_value_error(
    callback,
    message: str,
) -> None:
    try:
        callback()
    except ValueError:
        print(f"[OK] {message}")
        return

    raise AssertionError(
        f"未正確阻擋：{message}"
    )


def main() -> int:
    print("=" * 55)
    print("Discord RP 傳聞獎懲安全檢查")
    print("=" * 55)

    try:
        from core.rumor_reward_planner import (
            ALLOWED,
            RuleConfig,
            make_reward_plan,
        )

        check(
            callable(make_reward_plan),
            "獎懲計算函式可以正常載入",
        )

        check(
            "影響指數" not in ALLOWED,
            "影響指數禁止一般傳聞獎懲",
        )

        check(
            "CP好感度" not in ALLOWED,
            "CP好感度禁止一般傳聞獎懲",
        )

        if not ALLOWED:
            raise AssertionError(
                "沒有可供測試的一般數值欄位"
            )

        # 使用模組允許的第一個一般數值
        # 進行測試，不修改正式設定。
        stat_name = sorted(ALLOWED)[0]

        rules = RuleConfig(
            version="preflight-test-v1",
            grade_changes={
                "S": {stat_name: 10},
                "A": {stat_name: 8},
                "B": {stat_name: 5},
                "C": {stat_name: 2},
                "D": {stat_name: -3},
            },
            tier_percent={
                1: 100,
                2: 120,
                3: 150,
                4: 180,
                5: 200,
            },
            caps={
                stat_name: 100,
            },
            heat_reference=1000,
            max_heat_bonus_permille=200,
        )

        arguments = dict(
            guild_id="123456789",
            rumor_id=1001,
            character_id=2001,
            grade="A",
            tier=2,
            heat=500,
            rules=rules,
        )

        first = make_reward_plan(
            **arguments
        )

        second = make_reward_plan(
            **arguments
        )

        check(
            first.changes == second.changes,
            "相同規則產生相同獎懲結果",
        )

        check(
            first.fingerprint == second.fingerprint,
            "相同結算資料產生相同指紋",
        )

        check(
            isinstance(
                first.fingerprint,
                str,
            )
            and len(first.fingerprint) == 64,
            "SHA-256 結算指紋格式正確",
        )

        check(
            stat_name in first.changes,
            "一般數值獎懲計算正常",
        )

        check(
            abs(first.changes[stat_name])
            <= rules.caps[stat_name],
            "獎懲不超過單次上限",
        )

        expect_value_error(
            lambda: make_reward_plan(
                **{
                    **arguments,
                    "grade": "X",
                }
            ),
            "無效評級遭到拒絕",
        )

        expect_value_error(
            lambda: make_reward_plan(
                **{
                    **arguments,
                    "tier": 6,
                }
            ),
            "無效 T 等級遭到拒絕",
        )

        expect_value_error(
            lambda: make_reward_plan(
                **{
                    **arguments,
                    "heat": -1,
                }
            ),
            "負數熱度遭到拒絕",
        )

        forbidden_rules = RuleConfig(
            version="forbidden-test",
            grade_changes={
                "S": {"影響指數": 100},
                "A": {},
                "B": {},
                "C": {},
                "D": {},
            },
            tier_percent={
                1: 100,
                2: 100,
                3: 100,
                4: 100,
                5: 100,
            },
            caps={
                "影響指數": 100,
            },
            heat_reference=1000,
            max_heat_bonus_permille=200,
        )

        expect_value_error(
            lambda: make_reward_plan(
                **{
                    **arguments,
                    "rules": forbidden_rules,
                }
            ),
            "未授權影響指數獎勵遭到拒絕",
        )

        print()
        print("結果：PASS")
        print(
            "傳聞計算測試通過，"
            "未修改任何正式資料。"
        )

        return 0

    except Exception as exc:
        print()
        print(
            f"[ERROR] {type(exc).__name__}: "
            f"{exc}"
        )
        print("結果：FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
