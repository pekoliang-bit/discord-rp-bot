
"""娛樂城部署前唯讀靜態檢查。

在專案根目錄執行：python scripts/casino_integration_check.py
不登入 Discord，不變更資料庫或玩家金幣。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"

REQUIRED = (
    "casino_repository",
    "casino_service",
    "casino_scratch",
    "casino_lottery",
    "casino_gacha",
    "casino_games",
    "casino_effects",
    "casino_commands",
    "casino_shop_bridge",
    "casino_gacha_batch",
    "casino_wager_service",
    "casino_notice_dispatch",
    "casino_risk_bridge",
    "casino_private_notice",
    "casino_risk_executor",
    "casino_table_service",
    "casino_pvp_service",
)


def top_level_setup(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.AsyncFunctionDef)
        and node.name == "setup"
        for node in tree.body
    )


def command_names(tree: ast.AST) -> list[str]:
    names = []

    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue

            func = decorator.func

            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "command"
            ):
                continue

            for kw in decorator.keywords:
                if (
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    names.append(kw.value.value)

    return names


def check_loader(main_tree: ast.Module) -> list[str]:
    errors = []

    functions = {
        node.name: node
        for node in main_tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    }

    discover = functions.get("discover_extensions")

    if discover is None:
        return [
            "main.py 找不到 discover_extensions()"
        ]

    source = ast.unparse(discover)

    # 支援新版頂層 setup 掃描，
    # 以及舊版明確排除 casino 服務檔的方式。
    detects_setup = (
        "AsyncFunctionDef" in source
        and "setup" in source
    )

    skips_helpers = (
        "CASINO_HELPER_PREFIX" in source
        and "casino_commands" in source
    )

    if not (detects_setup or skips_helpers):
        errors.append(
            "main.py 載入流程未證明會排除"
            "沒有 setup() 的 casino 服務檔"
        )

    return errors


def inspect_project() -> list[str]:
    problems: list[str] = []

    main_file = ROOT / "main.py"

    if not main_file.is_file():
        return ["缺少 main.py"]

    try:
        main_tree = ast.parse(
            main_file.read_text(encoding="utf-8-sig"),
            filename=str(main_file),
        )
    except (
        OSError,
        UnicodeError,
        SyntaxError,
    ) as exc:
        return [f"main.py 無法解析：{exc}"]

    problems.extend(check_loader(main_tree))

    for name in REQUIRED:
        path = MODULES / (name + ".py")

        if not path.is_file():
            problems.append(
                f"缺少 modules/{name}.py"
            )
            continue

        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8-sig"),
                filename=str(path),
            )
        except (
            OSError,
            UnicodeError,
            SyntaxError,
        ) as exc:
            problems.append(
                f"無法解析 modules/{name}.py：{exc}"
            )
            continue

        has_setup = top_level_setup(tree)

        if name == "casino_commands" and not has_setup:
            problems.append(
                "casino_commands.py 缺少"
                "頂層 async setup(bot)"
            )

        if name != "casino_commands" and has_setup:
            problems.append(
                f"{name}.py 不應宣告頂層 "
                "async setup()，會被自動載入"
            )

    claimants: list[str] = []

    if MODULES.is_dir():
        for path in MODULES.glob("*.py"):
            try:
                tree = ast.parse(
                    path.read_text(encoding="utf-8-sig"),
                    filename=str(path),
                )
            except (
                OSError,
                UnicodeError,
                SyntaxError,
            ):
                continue

            names = command_names(tree)

            if "娛樂城" in names:
                claimants.append(path.name)

            if "娛樂城排行" in names:
                problems.append(
                    f"{path.name} 仍宣告額外頂層"
                    " /娛樂城排行"
                )

    if claimants != ["casino_commands.py"]:
        problems.append(
            "頂層 /娛樂城 應僅由 "
            "casino_commands.py 註冊："
            f"{claimants}"
        )

    return problems


def main() -> int:
    problems = inspect_project()

    if problems:
        print("[FAIL] 娛樂城靜態驗收未通過")

        for problem in problems:
            print(" -", problem)

        return 1

    print(
        "[PASS] 17 個娛樂城模組、載入條件與"
        "指令宣告靜態檢查通過"
    )
    print(
        "[NOTICE] 尚需金流、獎品、通知、"
        "Discord 實機驗證"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
