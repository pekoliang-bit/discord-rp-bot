
"""Discord RP 網路評論庫匯入工具。

執行方式：
    python -m scripts.import_comment_library

用途：
    1. 檢查已審核的評論資料
    2. 匯入 SQLite
    3. 防止重複匯入
    4. 顯示匯入結果及分類統計

注意：
    不會刪除玩家資料。
    不會自動發布 Discord 留言。
    不會修改角色屬性或金錢。
"""

from __future__ import annotations

import sys
from pathlib import Path


# 允許直接以 python scripts/import_comment_library.py 執行
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from core.comment_library import (
    SEED_PATH,
    import_approved,
    seed_catalog,
    stats,
)


CATEGORY_NAMES = {
    1: "日常生活",
    2: "影視作品",
    3: "演藝活動",
    4: "商業活動",
    5: "粉絲互動",
    6: "感情關係／CP",
    7: "爭議事件",
    8: "公關回應",
    9: "熱搜話題",
    10: "營銷號",
    11: "社會議題",
    12: "特殊事件",
}


INITIAL_TARGET_PER_CATEGORY = 80
EXPECTED_APPROVED_COUNT = 419


def validate_seed_file(path: Path) -> dict:
    """確認評論資料檔符合目前審核結果。"""

    if not path.is_file():
        raise FileNotFoundError(
            f"找不到評論資料檔：{path}"
        )

    catalog = seed_catalog(path)

    comments = catalog["approved"]

    if len(comments) != EXPECTED_APPROVED_COUNT:
        raise ValueError(
            "評論數量不符！"
            f"預期 {EXPECTED_APPROVED_COUNT} 則，"
            f"實際 {len(comments)} 則。"
        )

    comment_ids = {
        comment["id"]
        for comment in comments
    }

    if "C074" not in comment_ids:
        raise ValueError(
            "C074 已重新通過審核，不可遺漏。"
        )

    if "C300" in comment_ids:
        raise ValueError(
            "C300 已被排除，不可匯入。"
        )

    required_fields = {
        "id",
        "number",
        "batch",
        "text",
        "category_id",
        "category",
        "subcategory",
        "speaker",
        "emotion",
        "scene",
        "trigger_limit",
        "source",
        "canonical_id",
        "requires_authorization",
    }

    for comment in comments:
        missing = required_fields - comment.keys()

        if missing:
            raise ValueError(
                f"{comment.get('id', 'UNKNOWN')} "
                f"缺少必要欄位：{sorted(missing)}"
            )

        category_id = comment["category_id"]

        if category_id not in CATEGORY_NAMES:
            raise ValueError(
                f"{comment['id']} 主分類無效："
                f"{category_id}"
            )

        if not isinstance(comment["text"], str):
            raise TypeError(
                f"{comment['id']} 原文必須為字串。"
            )

        if not comment["text"].strip():
            raise ValueError(
                f"{comment['id']} 原文不可為空。"
            )

    return catalog


def print_category_statistics() -> None:
    """列出目前資料庫中的分類數量。"""

    current_stats = stats()

    total = sum(current_stats.values())

    print()
    print("=" * 62)
    print("網路評論庫分類統計")
    print("=" * 62)

    for category_id in range(1, 13):
        name = CATEGORY_NAMES[category_id]

        count = current_stats.get(category_id, 0)

        remaining = max(
            0,
            INITIAL_TARGET_PER_CATEGORY - count,
        )

        percentage = min(
            100.0,
            count / INITIAL_TARGET_PER_CATEGORY * 100,
        )

        status = (
            "已達標"
            if remaining == 0
            else f"尚缺 {remaining} 則"
        )

        print(
            f"{category_id:02d} "
            f"{name:<12} "
            f"{count:>4}/80 "
            f"{percentage:>6.1f}% "
            f"{status}"
        )

    target = (
        len(CATEGORY_NAMES)
        * INITIAL_TARGET_PER_CATEGORY
    )

    total_remaining = sum(
        max(
            0,
            INITIAL_TARGET_PER_CATEGORY
            - current_stats.get(category_id, 0),
        )
        for category_id in CATEGORY_NAMES
    )

    completed_categories = sum(
        1
        for category_id in CATEGORY_NAMES
        if current_stats.get(category_id, 0)
        >= INITIAL_TARGET_PER_CATEGORY
    )

    print("-" * 62)

    print(f"已確認評論總數：{total}")
    print(f"初始總目標：{target}")
    print(f"分類缺口合計：{total_remaining}")
    print(
        f"已達標分類："
        f"{completed_categories}/12"
    )

    print("=" * 62)


def main() -> int:
    """執行評論資料匯入。"""

    print("=" * 62)
    print("Discord RP 網路評論庫匯入工具")
    print("=" * 62)

    print(f"資料路徑：{SEED_PATH}")

    try:
        catalog = validate_seed_file(SEED_PATH)

        print()
        print("評論資料檢查通過。")

        print(
            "資料版本：",
            catalog["catalog_version"],
        )

        print(
            "本次已審核評論：",
            len(catalog["approved"]),
        )

        print("C074：已啟用")
        print("C300：已排除")

        print()
        print("開始匯入評論資料……")

        result = import_approved(SEED_PATH)

        print()
        print("匯入作業完成。")

        print(
            f"新增評論："
            f"{result['inserted']} 則"
        )

        print(
            f"既有評論："
            f"{result['unchanged']} 則"
        )

        print(
            f"本次處理總數："
            f"{result['total']} 則"
        )

        print_category_statistics()

        return 0

    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ) as exc:

        print()
        print("評論庫匯入失敗。")
        print(f"原因：{exc}")
        print("請先修正資料檔再重新執行。")

        return 1

    except Exception as exc:

        print()
        print("發生非預期錯誤。")
        print(
            f"{type(exc).__name__}: {exc}"
        )

        print("請檢查資料庫連線與原始碼。")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
