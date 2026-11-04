
"""Discord RP 評論庫部署前檢查。

執行：
    python -m scripts.check_comment_pipeline

本工具只讀取評論 JSON。
不連線 SQLite，不發送 Discord 訊息，
不修改任何玩家資料。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "assets" / "internet_comment_library"

EXPECTED_COUNT = 960
TARGET_PER_CATEGORY = 80

EXPECTED_FILES = [
    f"comments_part_{i:02d}.json"
    for i in range(1, 21)
]

CATEGORIES = {
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

REQUIRED = {
    "id",
    "number",
    "text",
    "category_id",
    "speaker",
    "emotion",
    "scene",
    "trigger_limit",
    "canonical_id",
    "requires_authorization",
}


def load_json(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        return json.load(file)


def check_manifest() -> list[Path]:
    """核對 manifest 與實際分檔。"""

    manifest_path = LIBRARY / "manifest.json"

    if not manifest_path.is_file():
        raise FileNotFoundError(
            "缺少 manifest.json"
        )

    manifest = load_json(manifest_path)
    parts = manifest.get("parts")

    if not isinstance(parts, list):
        raise ValueError(
            "manifest.parts 必須是清單"
        )

    names = []

    for part in parts:
        if isinstance(part, str):
            name = part
        elif isinstance(part, dict):
            name = part.get("file")
        else:
            raise ValueError(
                "manifest 分檔項目無效"
            )

        if not isinstance(name, str):
            raise ValueError(
                "manifest 檔案名稱無效"
            )

        names.append(name)

    if names != EXPECTED_FILES:
        raise ValueError(
            "manifest 的二十個分檔順序或名稱不正確"
        )

    actual = sorted(
        path.name
        for path in LIBRARY.glob(
            "comments_part_*.json"
        )
    )

    if actual != EXPECTED_FILES:
        raise ValueError(
            "實際評論分檔與預期不一致"
        )

    paths = [
        LIBRARY / name
        for name in names
    ]

    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少 {path.name}"
            )

    return paths


def load_comments(paths: list[Path]) -> list[dict]:
    """讀取並驗證所有分檔。"""

    comments = []

    for index, path in enumerate(paths, 1):
        data = load_json(path)

        if not isinstance(data, dict):
            raise ValueError(
                f"{path.name} 格式無效"
            )

        if data.get("part") != index:
            raise ValueError(
                f"{path.name} 的 part 編號不符"
            )

        records = data.get("approved")

        if not isinstance(records, list):
            raise ValueError(
                f"{path.name} 缺少 approved"
            )

        if not all(
            isinstance(item, dict)
            for item in records
        ):
            raise ValueError(
                f"{path.name} 包含非物件資料"
            )

        comments.extend(records)

        print(
            f"[OK] {path.name}: "
            f"{len(records)} 則"
        )

    return comments


def validate_comments(comments: list[dict]) -> Counter:
    """驗證 ID、原文、分類及審核狀態。"""

    if len(comments) != EXPECTED_COUNT:
        raise ValueError(
            f"預期 {EXPECTED_COUNT} 則，"
            f"實際 {len(comments)} 則"
        )

    ids = set()
    counts = Counter()

    for item in comments:
        missing = REQUIRED - item.keys()

        if missing:
            raise ValueError(
                f"{item.get('id', '?')} "
                f"缺少 {sorted(missing)}"
            )

        number = item.get("number")
        comment_id = item.get("id")

        if type(number) is not int:
            raise ValueError(
                f"{comment_id} 的 number 不是整數"
            )

        expected_id = f"C{number:03d}"

        if comment_id != expected_id:
            raise ValueError(
                f"編號不一致：{comment_id}"
            )

        if comment_id in ids:
            raise ValueError(
                f"重複 ID：{comment_id}"
            )

        ids.add(comment_id)

        category = item["category_id"]

        if (
            type(category) is not int
            or category not in CATEGORIES
        ):
            raise ValueError(
                f"{comment_id} 分類無效"
            )

        if (
            not isinstance(item["text"], str)
            or not item["text"].strip()
        ):
            raise ValueError(
                f"{comment_id} 原文無效"
            )

        if item.get(
            "status",
            "approved",
        ) != "approved":
            raise ValueError(
                f"{comment_id} 尚未通過審核"
            )

        if type(
            item["requires_authorization"]
        ) is not bool:
            raise ValueError(
                f"{comment_id} 授權欄位必須是布林值"
            )

        counts[category] += 1

    expected_ids = {
        f"C{i:03d}"
        for i in range(1, 962)
        if i != 300
    }

    if ids != expected_ids:
        raise ValueError(
            "ID 不符合 C001～C961 "
            "且排除 C300 的正式版本"
        )

    if "C074" not in ids or "C300" in ids:
        raise ValueError(
            "C074／C300 狀態錯誤"
        )

    return counts


def main() -> int:
    print("=" * 55)
    print("Discord RP 評論庫部署前檢查")
    print("=" * 55)

    try:
        paths = check_manifest()
        comments = load_comments(paths)
        counts = validate_comments(comments)

        print()
        print("分類完成進度")
        print("-" * 55)

        remaining_total = 0

        for category_id, name in CATEGORIES.items():
            count = counts[category_id]
            remaining = max(
                TARGET_PER_CATEGORY - count,
                0,
            )
            remaining_total += remaining

            print(
                f"{category_id:02d} {name}: "
                f"{count}/80，"
                f"尚缺 {remaining}"
            )

        print("-" * 55)
        print(
            f"驗證通過：{len(comments)} 則"
        )
        print(
            f"距離各分類達標尚缺："
            f"{remaining_total} 則"
        )

        print("結果：PASS")
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
