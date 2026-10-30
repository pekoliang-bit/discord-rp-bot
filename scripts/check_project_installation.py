
"""Discord RP 專案安裝檢查工具。

執行：
    python -m scripts.check_project_installation

只讀檢查：
1. Python 檔案語法
2. 評論庫九個分檔
3. manifest.json
4. 已核准評論的編號及數量
5. 核心系統檔案是否存在
6. Discord 擴充模組入口函式

不啟動 Discord。
不匯入業務模組。
不讀取或修改正式資料庫。
"""

from __future__ import annotations

import ast
import json
import sys

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

MODULES = ROOT / "modules"
CORE = ROOT / "core"

LIBRARY = (
    ROOT
    / "assets"
    / "internet_comment_library"
)

EXPECTED_COUNT = 419

REQUIRED_FILES = (
    "core/comment_library.py",
    "core/comment_event_rules.py",
    "core/rumor_reward_planner.py",
    "modules/internet_comment_bridge.py",
    "scripts/import_comment_library.py",
    "scripts/validate_comments.py",
    "scripts/audit_comment_categories.py",
    "assets/internet_comment_library/manifest.json",
)

EXPECTED_PARTS = tuple(
    f"comments_part_{i:02d}.json"
    for i in range(1, 10)
)

errors: list[str] = []
warnings: list[str] = []


def error(message: str) -> None:
    errors.append(message)
    print(f"[ERROR] {message}")


def warning(message: str) -> None:
    warnings.append(message)
    print(f"[WARN] {message}")


def success(message: str) -> None:
    print(f"[OK] {message}")


def check_required_files() -> None:
    print("\n=== 必要檔案 ===")

    for relative in REQUIRED_FILES:
        path = ROOT / relative

        if path.is_file():
            success(relative)
        else:
            error(f"缺少檔案：{relative}")


def check_python_syntax() -> None:
    print("\n=== Python 語法 ===")

    paths = sorted(
        ROOT.rglob("*.py")
    )

    count = 0

    for path in paths:

        if any(
            part in {
                ".venv",
                "venv",
                "__pycache__",
                ".git",
            }
            for part in path.parts
        ):
            continue

        count += 1

        try:
            source = path.read_text(
                encoding="utf-8-sig"
            )

            ast.parse(
                source,
                filename=str(path),
            )

        except (
            SyntaxError,
            UnicodeError,
            OSError,
        ) as exc:
            relative = path.relative_to(ROOT)
            error(
                f"語法或讀取錯誤："
                f"{relative}：{exc}"
            )

    success(
        f"已掃描 {count} 個 Python 檔案"
    )


def read_json(path: Path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as handle:
        return json.load(handle)


def check_comment_library() -> None:
    print("\n=== 網路評論庫 ===")

    if not LIBRARY.is_dir():
        error("找不到網路評論庫資料夾")
        return

    manifest_path = (
        LIBRARY / "manifest.json"
    )

    if not manifest_path.is_file():
        error("缺少 manifest.json")
        return

    try:
        manifest = read_json(
            manifest_path
        )
    except (
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        error(
            f"manifest.json 讀取失敗：{exc}"
        )
        return

    parts = manifest.get("parts")

    if not isinstance(parts, list):
        error(
            "manifest.json 的 parts 必須是清單"
        )
        return

    filenames: list[str] = []

    for item in parts:

        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("file")
        else:
            name = None

        if (
            not isinstance(name, str)
            or name not in EXPECTED_PARTS
        ):
            error(
                f"manifest 分檔名稱無效：{name}"
            )
            continue

        filenames.append(name)

    if (
        len(filenames) != 9
        or set(filenames) != set(EXPECTED_PARTS)
        or len(set(filenames)) != 9
    ):
        error(
            "manifest 未完整列出九個不重複分檔"
        )
        return

    success("manifest 九個分檔清單正確")

    approved: list[dict] = []

    for name in filenames:

        path = LIBRARY / name

        if not path.is_file():
            error(f"缺少評論分檔：{name}")
            continue

        try:
            data = read_json(path)
        except (
            OSError,
            UnicodeError,
            ValueError,
        ) as exc:
            error(
                f"{name} 讀取失敗：{exc}"
            )
            continue

        if not isinstance(data, dict):
            error(f"{name} 不是 JSON 物件")
            continue

        records = data.get("approved")

        if not isinstance(records, list):
            error(
                f"{name} 缺少 approved 清單"
            )
            continue

        if not all(
            isinstance(item, dict)
            for item in records
        ):
            error(
                f"{name} 有無效的評論物件"
            )
            continue

        approved.extend(records)

        success(
            f"{name}：{len(records)} 則"
        )

    if len(approved) != EXPECTED_COUNT:
        error(
            f"評論總數錯誤："
            f"{len(approved)}/{EXPECTED_COUNT}"
        )
        return

    ids: list[str] = []

    category_counts = {
        i: 0
        for i in range(1, 13)
    }

    for item in approved:

        comment_id = item.get("id")
        number = item.get("number")

        if (
            type(number) is not int
            or comment_id != f"C{number:03d}"
        ):
            error(
                f"評論編號無效：{comment_id}"
            )
            continue

        ids.append(comment_id)

        category_id = item.get(
            "category_id"
        )

        if (
            type(category_id) is not int
            or category_id not in category_counts
        ):
            error(
                f"{comment_id} 分類無效"
            )
        else:
            category_counts[category_id] += 1

        if (
            not isinstance(
                item.get("text"),
                str,
            )
            or not item["text"].strip()
        ):
            error(
                f"{comment_id} 原文無效"
            )

        if item.get(
            "status",
            "approved",
        ) != "approved":
            error(
                f"{comment_id} 尚未審核"
            )

    expected_ids = {
        f"C{i:03d}"
        for i in range(1, 421)
        if i != 300
    }

    if set(ids) != expected_ids:
        error(
            "評論 ID 集合不符合 "
            "C001～C420（排除 C300）"
        )
    elif len(ids) != len(set(ids)):
        error("評論 ID 重複")
    else:
        success(
            "419 則評論編號正確，"
            "C074 保留、C300 排除"
        )

    print("\n分類統計：")

    for category_id, count in (
        category_counts.items()
    ):
        remaining = max(
            80 - count,
            0,
        )

        print(
            f"{category_id:02d}："
            f"{count} 則，"
            f"尚缺 {remaining} 則"
        )


def check_extensions() -> None:
    print("\n=== Discord 擴充模組 ===")

    if not MODULES.is_dir():
        error("找不到 modules 資料夾")
        return

    extensions = 0
    utilities = 0

    for path in sorted(
        MODULES.glob("*.py")
    ):

        if path.name.startswith("_"):
            continue

        try:
            tree = ast.parse(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )
        except (
            SyntaxError,
            UnicodeError,
            OSError,
        ):
            # 語法問題已由上一階段記錄。
            continue

        has_setup = any(
            isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
            and node.name == "setup"
            for node in tree.body
        )

        if has_setup:
            extensions += 1
        else:
            utilities += 1

    success(
        f"含 setup 的模組：{extensions}"
    )

    success(
        f"共用功能模組：{utilities}"
    )

    if utilities:
        warning(
            "main.py 必須跳過沒有 "
            "setup() 的共用模組"
        )


def main() -> int:
    print("=" * 56)
    print("Discord RP 安裝與語法檢查")
    print("=" * 56)

    print(f"專案位置：{ROOT}")

    check_required_files()
    check_python_syntax()
    check_comment_library()
    check_extensions()

    print("\n" + "=" * 56)

    print(
        f"錯誤：{len(errors)} 項"
    )

    print(
        f"提醒：{len(warnings)} 項"
    )

    if errors:
        print(
            "結果：FAILED"
        )
        return 1

    if warnings:
        print(
            "結果：PASS_WITH_WARNINGS"
        )
        return 0

    print("結果：PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
