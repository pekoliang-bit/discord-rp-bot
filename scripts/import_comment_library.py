
"""Discord RP 網路評論庫匯入工具。

執行：
    python -m scripts.import_comment_library

功能：
- 讀取 manifest 列出的所有 JSON 分檔
- 檢查 960 則已審核評論（兼容舊版 419 則）
- 確認 C074 存在、C300 排除
- 匯入 SQLite
- 拒絕覆蓋內容不同的舊評論
- 列出 12 大分類完成進度

本程式不會自動發布 Discord 訊息，
也不會修改玩家、金錢或角色屬性。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from core.comment_library import (
    SEED_PATH,
    CATEGORIES,
    import_approved,
    seed_catalog,
    stats,
)


EXPECTED_APPROVED_COUNT = 960
INITIAL_TARGET_PER_CATEGORY = 80

REQUIRED_FIELDS = {
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


def validate_seed_file(
    path: Path = SEED_PATH,
) -> dict:
    """完整驗證評論資料來源。

    支援：
    - 評論資料夾（含多個 JSON 分檔）
    - 舊版單一 comments.json

    僅檢查資料，不修改資料庫。
    """

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"評論資料路徑不存在：{path}"
        )

    if path.is_dir():
        parts = sorted(
            path.glob("comments_part_*.json")
        )

        if not parts:
            raise FileNotFoundError(
                "評論資料夾內沒有 JSON 分檔。"
            )

        print(
            f"偵測到 {len(parts)} 個 JSON 分檔。"
        )

    elif path.is_file():
        if path.suffix.lower() != ".json":
            raise ValueError(
                "評論資料檔必須是 JSON 格式。"
            )
    else:
        raise ValueError(
            "評論資料路徑不是檔案或資料夾。"
        )

    catalog = seed_catalog(path)

    comments = catalog["approved"]

    expected_count = EXPECTED_APPROVED_COUNT
    if len(comments) != expected_count:
        raise ValueError(
            "評論數量不符合目前正式審核版本："
            f"預期 {expected_count}，"
            f"實際 {len(comments)}。"
        )

    seen_ids = set()
    seen_numbers = set()

    counts = Counter()

    for comment in comments:

        if not isinstance(comment, dict):
            raise TypeError(
                "評論資料必須是 JSON 物件。"
            )

        missing = REQUIRED_FIELDS - comment.keys()

        if missing:
            raise ValueError(
                f"{comment.get('id', 'UNKNOWN')} "
                f"缺少欄位：{sorted(missing)}"
            )

        comment_id = comment["id"]
        number = comment["number"]

        if type(number) is not int:
            raise TypeError(
                f"{comment_id} 的 number 必須是整數。"
            )

        if comment_id != f"C{number:03d}":
            raise ValueError(
                f"評論編號格式不正確：{comment_id}"
            )

        if comment_id in seen_ids:
            raise ValueError(
                f"重複評論 ID：{comment_id}"
            )

        if number in seen_numbers:
            raise ValueError(
                f"重複數字編號：{number}"
            )

        seen_ids.add(comment_id)
        seen_numbers.add(number)

        category_id = comment["category_id"]

        if (
            type(category_id) is not int
            or category_id not in CATEGORIES
        ):
            raise ValueError(
                f"{comment_id} 主分類無效。"
            )

        if (
            not isinstance(comment["text"], str)
            or not comment["text"].strip()
        ):
            raise ValueError(
                f"{comment_id} 的評論原文無效。"
            )

        if comment.get("status", "approved") != "approved":
            raise ValueError(
                f"{comment_id} 尚未通過審核。"
            )

        counts[category_id] += 1

    if "C074" not in seen_ids:
        raise ValueError(
            "C074 必須保留在已核准評論庫。"
        )

    if "C300" in seen_ids:
        raise ValueError(
            "C300 已被排除，不得匯入。"
        )

    end_number = 961 if len(comments) == 960 else 420
    expected_ids = {
        f"C{number:03d}"
        for number in range(1, end_number + 1)
        if number != 300
    }

    if seen_ids != expected_ids:
        missing = sorted(expected_ids - seen_ids)
        extra = sorted(seen_ids - expected_ids)

        raise ValueError(
            f"評論編號不符合目前版本。"
            f"缺少：{missing}；"
            f"多出：{extra}"
        )

    print(
        f"資料來源驗證成功：{len(comments)} 則。"
    )

    print(
        f"已識別分類：{len(counts)} 個。"
    )

    return catalog


def print_category_statistics() -> None:
    """顯示 SQLite 內的正式評論分類數量。"""

    current = stats()

    print()
    print("=" * 62)
    print("網路評論庫分類進度")
    print("=" * 62)

    total = 0
    remaining_total = 0
    completed_categories = 0

    for category_id in range(1, 13):

        name = CATEGORIES[category_id]

        count = current.get(category_id, 0)

        remaining = max(
            INITIAL_TARGET_PER_CATEGORY - count,
            0,
        )

        total += count
        remaining_total += remaining

        if remaining == 0:
            completed_categories += 1

        percent = min(
            count / INITIAL_TARGET_PER_CATEGORY * 100,
            100,
        )

        print(
            f"{category_id:02d} "
            f"{name:<12} "
            f"{count:>3}/80 "
            f"{percent:>5.1f}% "
            f"剩餘 {remaining:>3} 則"
        )

    print("-" * 62)

    print(f"資料庫已啟用評論：{total}")

    print(
        "初始目標：",
        12 * INITIAL_TARGET_PER_CATEGORY,
    )

    print(
        f"距離各分類達標尚缺："
        f"{remaining_total} 則"
    )

    print(
        f"已達標分類："
        f"{completed_categories}/12"
    )

    print("=" * 62)


def main() -> int:
    """檢查、匯入並顯示結果。"""

    print("=" * 62)
    print("Discord RP 網路評論庫匯入")
    print("=" * 62)

    print(f"評論資料來源：{SEED_PATH}")

    try:
        catalog = validate_seed_file(SEED_PATH)

        print()
        print(
            "資料版本：",
            catalog["catalog_version"],
        )

        print(
            "已核准評論：",
            len(catalog["approved"]),
        )

        print("C074：保留")
        print("C300：排除")

        print()
        print("正在匯入 SQLite……")

        result = import_approved(SEED_PATH)

        print()
        print("評論庫匯入成功。")

        print(
            f"新增：{result['inserted']} 則"
        )

        print(
            f"已存在：{result['unchanged']} 則"
        )

        print(
            f"處理總數：{result['total']} 則"
        )

        print_category_statistics()

        return 0

    except Exception as exc:

        print()
        print("評論庫匯入失敗。")

        print(
            f"{type(exc).__name__}: {exc}"
        )

        print(
            "本工具不會自行覆蓋"
            "內容不同的已存在評論。"
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
