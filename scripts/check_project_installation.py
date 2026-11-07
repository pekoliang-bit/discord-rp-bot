
"""Discord RP 專案安裝檢查工具。

執行：
    python -m scripts.check_project_installation

只讀檢查：
1. Python 檔案語法
2. 最新960則評論庫分檔
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

EXPECTED_COUNT = 960

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
    for i in list(range(1, 11)) + list(range(11, 21)) if i != 10
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
    """Validate the current approved catalog without relying on outdated part counts."""
    print("\n=== 網路評論庫 ===")
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from core.comment_library import seed_catalog, CATEGORIES
        catalog = seed_catalog(LIBRARY)
        approved = catalog["approved"]
        counts = {category: 0 for category in CATEGORIES}
        for entry in approved:
            counts[entry["category_id"]] += 1
        if len(approved) != 960 or any(value != 80 for value in counts.values()):
            error(f"評論數量不足：共 {len(approved)} 則，分類統計 {counts}")
        else:
            success("960 則已核准評論、12 個分類各 80 則，分檔與 ID 檢查通過")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error(f"評論資料檢查失敗：{exc}")


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
