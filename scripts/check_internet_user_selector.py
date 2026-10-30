
"""Discord RP 固定網友出場機制驗收。

執行：
    python -m scripts.check_internet_user_selector

本測試只使用記憶體中的虛構帳號：
- 不操作 SQLite
- 不修改角色資料
- 不發送 Discord 訊息
- 不建立正式粉絲或黑粉
"""

from __future__ import annotations

import math
import random
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from core.internet_user_selector import (
    SelectionPolicy,
    calculate_weight,
    normalize_commenter,
    select_commenters,
)


def make_user(
    uid: int,
    relation: str,
    weight: int = 30,
    familiarity: int = 50,
    influence: int = 20,
) -> dict:
    return {
        "id": uid,
        "username": f"測試網友{uid}",
        "account_type": relation,
        "relation_type": relation,
        "appearance_weight": weight,
        "familiarity": familiarity,
        "influence": influence,
        "appearance_count": 0,
        "last_appearance_at": None,
        "active": 1,
    }


USERS = [
    make_user(1, "fan", 40),
    make_user(2, "fan", 30),
    make_user(3, "superfan", 80),
    make_user(4, "fansite", 70),
    make_user(5, "solo_fan", 60),
    make_user(6, "neutral", 25),
    make_user(7, "neutral", 20),
    make_user(8, "cp_fan", 30),
    make_user(9, "anti", 75),
    make_user(10, "anti", 65),
    make_user(11, "anti", 50),
    make_user(12, "fan", 20),
]


def require(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)

    print(f"[PASS] {message}")


def expect_value_error(callback, message: str):
    try:
        callback()
    except ValueError:
        print(f"[PASS] {message}")
        return

    raise AssertionError(
        f"應拒絕但未拒絕：{message}"
    )


def test_basic_selection():
    result = select_commenters(
        "TEST-EVENT-001",
        "performance",
        USERS,
        policy=SelectionPolicy(
            max_per_event=8,
            max_anti_ratio=0.25,
        ),
        rng=random.Random(42),
    )

    ids = [
        user.id
        for user in result.selected
    ]

    require(
        len(ids) <= 8,
        "出場人數不超過設定上限",
    )

    require(
        len(ids) == len(set(ids)),
        "同一事件不重複選到相同網友",
    )

    anti_count = sum(
        user.relation_type == "anti"
        for user in result.selected
    )

    require(
        anti_count <= math.floor(
            8 * 0.25
        ),
        "固定黑粉數量不超過比例限制",
    )

    require(
        result.considered == len(USERS),
        "候選人數量記錄正確",
    )


def test_reproducible():
    first = select_commenters(
        "TEST-EVENT-002",
        "birthday",
        USERS,
        rng=random.Random(12345),
    )

    second = select_commenters(
        "TEST-EVENT-002",
        "birthday",
        USERS,
        rng=random.Random(12345),
    )

    first_ids = [
        user.id
        for user in first.selected
    ]

    second_ids = [
        user.id
        for user in second.selected
    ]

    require(
        first_ids == second_ids,
        "相同隨機種子可重現選取結果",
    )


def test_cooldown():
    user = normalize_commenter(
        make_user(
            30,
            "superfan",
            weight=80,
        )
    )

    normal = calculate_weight(
        user,
        "birthday",
    )

    recent = calculate_weight(
        user,
        "birthday",
        recent_event_ids={30},
    )

    require(
        recent < normal,
        "近期已出場帳號的權重下降",
    )

    require(
        math.isclose(
            recent / normal,
            0.2,
        ),
        "近期冷卻倍率正確",
    )


def test_fansite_bonus():
    fansite = normalize_commenter(
        make_user(
            40,
            "fansite",
            weight=50,
        )
    )

    concert = calculate_weight(
        fansite,
        "performance",
    )

    ordinary = calculate_weight(
        fansite,
        "daily",
    )

    require(
        concert > ordinary,
        "站姐在演出事件更容易出現",
    )


def test_no_anti():
    result = select_commenters(
        "TEST-EVENT-003",
        "daily",
        USERS,
        policy=SelectionPolicy(
            max_per_event=10,
            allow_anti=False,
        ),
        rng=random.Random(99),
    )

    require(
        all(
            user.relation_type != "anti"
            for user in result.selected
        ),
        "禁止黑粉時不選取黑粉",
    )


def test_inactive_and_zero_weight():
    inactive = make_user(
        50,
        "fan",
        weight=100,
    )
    inactive["active"] = 0

    zero = make_user(
        51,
        "fan",
        weight=0,
    )

    result = select_commenters(
        "TEST-EVENT-004",
        "daily",
        [inactive, zero],
        rng=random.Random(1),
    )

    require(
        not result.selected,
        "停用與零權重帳號不出場",
    )

    require(
        result.excluded == 2,
        "排除數量正確",
    )


def test_invalid_inputs():
    expect_value_error(
        lambda: select_commenters(
            "",
            "daily",
            USERS,
        ),
        "空事件 ID 遭到拒絕",
    )

    expect_value_error(
        lambda: select_commenters(
            "TEST",
            "unknown_event",
            USERS,
        ),
        "未知事件類型遭到拒絕",
    )

    expect_value_error(
        lambda: select_commenters(
            "TEST",
            "daily",
            USERS,
            policy=SelectionPolicy(
                max_anti_ratio=1.5,
            ),
        ),
        "非法黑粉比例遭到拒絕",
    )

    expect_value_error(
        lambda: normalize_commenter(
            make_user(
                100,
                "unknown",
            )
        ),
        "非法網友關係遭到拒絕",
    )


def main() -> int:
    print("=" * 60)
    print("Discord RP 固定網友出場驗收")
    print("=" * 60)

    tests = (
        test_basic_selection,
        test_reproducible,
        test_cooldown,
        test_fansite_bonus,
        test_no_anti,
        test_inactive_and_zero_weight,
        test_invalid_inputs,
    )

    failures = []

    for test in tests:
        print()
        print(f"測試：{test.__name__}")

        try:
            test()
        except Exception as exc:
            failures.append(
                (test.__name__, str(exc))
            )
            print(
                f"[FAIL] "
                f"{type(exc).__name__}: {exc}"
            )

    print()
    print("=" * 60)

    passed = len(tests) - len(failures)

    print(
        f"通過：{passed}/{len(tests)}"
    )

    if failures:
        print("結果：FAILED")

        for name, message in failures:
            print(
                f"{name}: {message}"
            )

        return 1

    print("結果：PASS")
    print(
        "固定網友出場規則驗收通過。"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
