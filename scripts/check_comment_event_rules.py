
"""Discord RP 評論事件規則安全測試。

執行：
    python -m scripts.check_comment_event_rules

不連線資料庫、不修改玩家資料、
不發布任何 Discord 留言。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from core.comment_event_rules import (
    VerifiedCommentContext,
    allowed_comment_ids,
)


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(
            f"{label}\n"
            f"預期：{expected!r}\n"
            f"實際：{actual!r}"
        )

    print(f"[PASS] {label}")


def assert_value_error(callback, label):
    try:
        callback()
    except ValueError:
        print(f"[PASS] {label}")
        return

    raise AssertionError(
        f"未阻擋非法輸入：{label}"
    )


def make_row(
    comment_id: str,
    category_id: int,
    *,
    approved: bool = True,
    authorization: bool = False,
) -> dict:
    return {
        "id": comment_id,
        "category_id": category_id,
        "approved": int(approved),
        "requires_authorization": int(
            authorization
        ),
    }


def test_performance():
    row = make_row("C391", 3)

    ctx = VerifiedCommentContext(
        event_id="TEST-CONCERT-001",
        category_id=3,
        event_verified=True,
        facts=frozenset({
            "performance",
            "ticket_paid",
        }),
        reviewed_comment_ids=frozenset({
            "C391",
        }),
    )

    result = allowed_comment_ids(
        ctx,
        [row],
    )

    assert_equal(
        result,
        frozenset({"C391"}),
        "已核實的付費演出可使用 C391",
    )

    assert_equal(
        allowed_comment_ids(
            replace(
                ctx,
                event_verified=False,
            ),
            [row],
        ),
        frozenset(),
        "未核實事件不得抽取評論",
    )

    assert_equal(
        allowed_comment_ids(
            replace(
                ctx,
                facts=frozenset({
                    "performance",
                }),
            ),
            [row],
        ),
        frozenset(),
        "沒有付費票券不得使用 C391",
    )

    assert_equal(
        allowed_comment_ids(
            replace(
                ctx,
                reviewed_comment_ids=frozenset(),
            ),
            [row],
        ),
        frozenset(),
        "未列入事件白名單不得使用",
    )

    assert_equal(
        allowed_comment_ids(
            ctx,
            [
                make_row(
                    "C391",
                    3,
                    approved=False,
                )
            ],
        ),
        frozenset(),
        "未核准評論不得使用",
    )

    assert_equal(
        allowed_comment_ids(
            ctx,
            [make_row("C391", 5)],
        ),
        frozenset(),
        "分類不符合時不得使用",
    )


def test_authorization():
    ctx = VerifiedCommentContext(
        event_id="TEST-BIRTHDAY-001",
        category_id=12,
        event_verified=True,
        facts=frozenset({
            "birthday",
            "birthday_post",
        }),
        reviewed_comment_ids=frozenset({
            "C325",
        }),
        authorized_speaker=False,
    )

    row = make_row("C325", 12)

    assert_equal(
        allowed_comment_ids(ctx, [row]),
        frozenset(),
        "普通網友不得冒用角色本人留言",
    )

    authorized = replace(
        ctx,
        authorized_speaker=True,
    )

    assert_equal(
        allowed_comment_ids(
            authorized,
            [row],
        ),
        frozenset({"C325"}),
        "授權角色發言可通過事件規則",
    )

    assert_equal(
        allowed_comment_ids(
            authorized,
            [
                make_row(
                    "C325",
                    12,
                    authorization=True,
                )
            ],
        ),
        frozenset({"C325"}),
        "授權標記與角色授權均可識別",
    )


def test_allegation():
    ctx = VerifiedCommentContext(
        event_id="TEST-RUMOR-001",
        category_id=7,
        event_verified=True,
        facts=frozenset({
            "public_controversy",
            "verified_evidence",
        }),
        reviewed_comment_ids=frozenset({
            "C346",
        }),
        allegation_use_approved=False,
    )

    row = make_row("C346", 7)

    assert_equal(
        allowed_comment_ids(ctx, [row]),
        frozenset(),
        "未授權爭議不得使用指控評論",
    )

    approved_context = replace(
        ctx,
        allegation_use_approved=True,
    )

    assert_equal(
        allowed_comment_ids(
            approved_context,
            [row],
        ),
        frozenset({"C346"}),
        "已授權且有證據的爭議符合規則",
    )

    assert_equal(
        allowed_comment_ids(
            replace(
                approved_context,
                facts=frozenset({
                    "public_controversy",
                }),
            ),
            [row],
        ),
        frozenset(),
        "沒有已驗證證據不得使用 C346",
    )


def test_unknown_and_unreviewed():
    ctx = VerifiedCommentContext(
        event_id="TEST-UNKNOWN-001",
        category_id=3,
        event_verified=True,
        facts=frozenset({
            "performance",
            "ticket_paid",
        }),
        reviewed_comment_ids=frozenset({
            "C391",
            "C999",
        }),
    )

    assert_equal(
        allowed_comment_ids(
            ctx,
            [
                make_row("C999", 3),
            ],
        ),
        frozenset(),
        "未建立個別規則的評論不得自動使用",
    )

    assert_value_error(
        lambda: allowed_comment_ids(
            replace(
                ctx,
                facts=frozenset({
                    "unknown_unverified_fact",
                }),
            ),
            [
                make_row("C391", 3),
            ],
        ),
        "未知事件事實必須遭到拒絕",
    )


def main() -> int:
    print("=" * 58)
    print("Discord RP 評論事件規則測試")
    print("=" * 58)

    tests = (
        test_performance,
        test_authorization,
        test_allegation,
        test_unknown_and_unreviewed,
    )

    passed = 0

    for test in tests:
        print()
        print(f"正在測試：{test.__name__}")

        try:
            test()
            passed += 1
        except Exception as exc:
            print(
                f"[FAIL] "
                f"{type(exc).__name__}: {exc}"
            )

    print()
    print("=" * 58)
    print(
        f"測試組別：{passed}/{len(tests)}"
    )

    if passed != len(tests):
        print("結果：FAILED")
        return 1

    print("結果：PASS")
    print(
        "事件規則測試通過，"
        "沒有更動正式資料。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
