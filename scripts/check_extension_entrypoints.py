
"""Discord RP 擴充模組入口檢查器。

執行：
    python -m scripts.check_extension_entrypoints

目的：
- 靜態辨識 Discord 擴充模組
- 檢查 setup() 是否為 async def
- 檢查 setup() 必要參數
- 比對 main.py 的模組探索規則
- 防止 NoEntryPointError 再次發生

不執行 main.py，不登入 Discord。
不修改資料庫與玩家資料。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
MAIN = ROOT / "main.py"

PRIORITY = (
    "modules.shop",
    "modules.player_shops",
    "modules.storefront",
    "modules.shop_integration",
)

FINAL = (
    "modules.zz_owner_review_policy",
)

IGNORED = {
    "test_character",
    "test_content_manager",
}


def parse_file(path: Path) -> ast.Module:
    return ast.parse(
        path.read_text(encoding="utf-8-sig"),
        filename=str(path),
    )


def find_setup(
    tree: ast.Module,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ) and node.name == "setup":
            return node
    return None


def check_main_discovery() -> list[str]:
    issues = []

    if not MAIN.is_file():
        return ["缺少 main.py"]

    try:
        tree = parse_file(MAIN)
    except (SyntaxError, OSError, UnicodeError) as exc:
        return [f"main.py 無法解析：{exc}"]

    discover = next(
        (
            node for node in tree.body
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name == "discover_extensions"
        ),
        None,
    )

    if discover is None:
        return ["main.py 缺少 discover_extensions()"]

    # 確認探索函式有針對 setup 做檢查。
    has_setup_check = any(
        isinstance(node, ast.Compare)
        and any(
            isinstance(value, ast.Constant)
            and value.value == "setup"
            for value in ast.walk(node)
        )
        for node in ast.walk(discover)
    )

    # 與上方不同寫法的 setup 比較，
    # 可透過完整 AST 再確認。
    if not has_setup_check:
        has_setup_check = any(
            isinstance(node, ast.Constant)
            and node.value == "setup"
            for node in ast.walk(discover)
        )

    if not has_setup_check:
        issues.append(
            "discover_extensions() 未發現 setup 入口檢查"
        )

    return issues


def main() -> int:
    print("=" * 60)
    print("Discord RP 擴充模組入口檢查")
    print("=" * 60)

    if not MODULES.is_dir():
        print("[ERROR] 找不到 modules 資料夾")
        return 1

    errors = check_main_discovery()

    extensions: list[str] = []
    utilities: list[str] = []

    for path in sorted(MODULES.glob("*.py")):
        if (
            path.name.startswith("_")
            or path.stem in IGNORED
        ):
            continue

        module_name = f"modules.{path.stem}"

        try:
            tree = parse_file(path)
        except (SyntaxError, OSError, UnicodeError) as exc:
            errors.append(
                f"{module_name} 無法解析：{exc}"
            )
            continue

        setup = find_setup(tree)

        if setup is None:
            utilities.append(module_name)
            continue

        extensions.append(module_name)

        if not isinstance(setup, ast.AsyncFunctionDef):
            errors.append(
                f"{module_name} 的 setup 必須使用 async def"
            )
            continue

        positional = (
            list(setup.args.posonlyargs)
            + list(setup.args.args)
        )

        if not positional:
            errors.append(
                f"{module_name} 的 setup 缺少 bot 參數"
            )

    print()
    print(f"找到正式擴充模組：{len(extensions)}")
    print(f"找到一般共用模組：{len(utilities)}")

    print()
    print("=== 需要載入的擴充模組 ===")

    ordered = (
        [
            name
            for name in extensions
            if name not in PRIORITY
            and name not in FINAL
        ]
        + [
            name for name in PRIORITY
            if name in extensions
        ]
        + [
            name for name in FINAL
            if name in extensions
        ]
    )

    for name in ordered:
        print(f"[EXTENSION] {name}")

    print()
    print("=== 不應直接使用 load_extension 的模組 ===")

    for name in utilities:
        print(f"[UTILITY] {name}")

    print()
    print("=== 檢查結果 ===")

    if errors:
        for issue in errors:
            print(f"[ERROR] {issue}")

        print(f"結果：FAILED（{len(errors)} 項錯誤）")
        return 1

    print("[OK] 所有已辨識擴充模組具有正確入口")
    print("[OK] 共用模組已與擴充模組區分")
    print("結果：PASS")

    print()
    print(
        "注意：這是靜態檢查，"
        "不能取代 Railway 實際啟動測試。"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
