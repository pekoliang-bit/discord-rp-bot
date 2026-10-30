
"""Discord RP 網路評論橋接模組安全測試。

執行：
    python -m scripts.check_comment_bridge

使用記憶體中的模擬資料，不讀寫正式 SQLite。
不連接 Discord，不發送留言。
"""

from __future__ import annotations

import random
import sys

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from modules.internet_comment_bridge import (
    CommentEvent,
    preview_comment,
    preview_by_id,
)


def make_row(
    comment_id: str,
    text: str,
    *,
    category_id: int = 3,
    speaker: str = "普通網友",
    emotion: str = "正面",
    requires_authorization: int = 0,
) -> dict:
    return {
        "id": comment_id,
        "raw_text": text,
        "category_id": category_id,
        "speaker": speaker,
        "emotion": emotion,
        "requires_authorization": (
            requires_authorization
        ),
        "trigger_limit": "測試用事件條件",
        "approved": 1,
    }


MOCK_ROWS = [
    make_row(
        "C038",
        "美神降臨",
    ),
    make_row(
        "C074",
        "美神降臨",
    ),
    make_row(
        "C391",
        "看完現場才知道什麼叫實力，這票買得太值了！",
    ),
    make_row(
        "C999",
        "ooo今天的演出真精彩！",
    ),
    make_row(
        "C998",
        "今天的舞台表現令人失望。",
        emotion="負面",
    ),
    make_row(
        "C325",
        "今天謝謝大家的祝福！",
        category_id=12,
        speaker="角色本人",
        requires_authorization=1,
    ),
]


def mock_list_templates(
    category_id=None,
    *,
    limit=50,
    offset=0,
    allow_authorized=False,
):
    """模擬資料庫分頁查詢。"""

    result = []

    for row in MOCK_ROWS:
        if (
            category_id is not None
            and row["category_id"] != category_id
        ):
            continue

        if (
            row["requires_authorization"]
            and not allow_authorized
        ):
            continue

        result.append(dict(row))

    return result[offset:offset + limit]


def check(condition, label):
    if not condition:
        raise AssertionError(label)

    print(f"[PASS] {label}")


def test_unverified():
    event = CommentEvent(
        event_id="TEST-001",
        category_id=3,
        verified=False,
        permitted_comment_ids=frozenset({
            "C391",
        }),
    )

    check(
        preview_comment(event) is None,
        "未核實事件不產生留言",
    )

    check(
        preview_comment(
            replace(
                event,
                verified=True,
                permitted_comment_ids=frozenset(),
            )
        ) is None,
        "沒有允許的評論 ID 不產生留言",
    )


def test_allowed_id():
    event = CommentEvent(
        event_id="TEST-002",
        category_id=3,
        verified=True,
        permitted_comment_ids=frozenset({
            "C391",
        }),
    )

    result = preview_comment(
        event,
        rng=random.Random(7),
    )

    check(
        result is not None,
        "合法事件能生成評論預覽",
    )

    check(
        result.comment_id == "C391",
        "只會使用事件允許的評論 ID",
    )

    check(
        result.rendered == MOCK_ROWS[2]["raw_text"],
        "評論原文正常輸出",
    )

    check(
        preview_by_id(
            event,
            "C998",
        ) is None,
        "指定未授權 ID 時拒絕預覽",
    )


def test_authorization():
    event = CommentEvent(
        event_id="TEST-003",
        category_id=12,
        verified=True,
        permitted_comment_ids=frozenset({
            "C325",
        }),
        speaker_authorized=False,
    )

    check(
        preview_comment(event) is None,
        "普通網友不能冒用角色本人",
    )

    authorized_event = replace(
        event,
        speaker_authorized=True,
    )

    result = preview_comment(
        authorized_event
    )

    check(
        result is not None
        and result.comment_id == "C325",
        "取得授權後才能預覽角色本人留言",
    )


def test_emotion():
    event = CommentEvent(
        event_id="TEST-004",
        category_id=3,
        verified=True,
        permitted_comment_ids=frozenset({
            "C391",
            "C998",
        }),
        emotion_keywords=("負面",),
    )

    result = preview_comment(event)

    check(
        result is not None
        and result.comment_id == "C998",
        "情緒篩選符合指定條件",
    )


def test_placeholder():
    event = CommentEvent(
        event_id="TEST-005",
        category_id=3,
        verified=True,
        permitted_comment_ids=frozenset({
            "C999",
        }),
        primary_name="司笑瞳",
    )

    result = preview_comment(event)

    check(
        result is not None
        and "司笑瞳" in result.rendered,
        "角色名稱變數替換成功",
    )

    missing_name = replace(
        event,
        primary_name=None,
    )

    check(
        preview_comment(missing_name) is None,
        "缺少角色名稱時不輸出未替換變數",
    )


def test_duplicates():
    event = CommentEvent(
        event_id="TEST-006",
        category_id=3,
        verified=True,
        permitted_comment_ids=frozenset({
            "C038",
            "C074",
        }),
    )

    seen = set()

    for seed in range(20):
        result = preview_comment(
            event,
            rng=random.Random(seed),
        )

        check(
            result is not None,
            "重複模板測試取得有效預覽",
        )

        seen.add(result.rendered)

    check(
        seen == {"美神降臨"},
        "C038 與 C074 不產生不同的原文結果",
    )


def main() -> int:
    print("=" * 58)
    print("Discord RP 評論橋接安全測試")
    print("=" * 58)

    tests = (
        test_unverified,
        test_allowed_id,
        test_authorization,
        test_emotion,
        test_placeholder,
        test_duplicates,
    )

    failures = []

    # 只在此區塊期間替換查詢函式。
    # 離開區塊後會自動還原。
    with patch(
        "modules.internet_comment_bridge."
        "comment_library.list_templates",
        side_effect=mock_list_templates,
    ):
        for test in tests:
            print()
            print(f"正在測試：{test.__name__}")

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
    print("=" * 58)

    print(
        f"測試通過："
        f"{len(tests) - len(failures)}"
        f"/{len(tests)}"
    )

    if failures:
        print("結果：FAILED")
        for name, reason in failures:
            print(f"- {name}: {reason}")
        return 1

    print("結果：PASS")
    print(
        "橋接模組安全測試完成；"
        "未修改正式資料庫。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
