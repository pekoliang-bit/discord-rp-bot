
"""娛樂城原始碼唯讀驗收。

放置：scripts/casino_code_audit.py
執行：python scripts/casino_code_audit.py
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "modules"

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


def audit() -> int:
    errors = []
    warnings = []
    trees = {}

    for name in REQUIRED:
        path = MOD / f"{name}.py"

        if not path.is_file():
            errors.append(f"缺少 modules/{name}.py")
            continue

        try:
            trees[name] = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"{name}: {exc}")

    commands = trees.get("casino_commands")

    if commands:
        funcs = {
            node.name
            for node in ast.walk(commands)
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
        }

        for name in ("_characters", "setup"):
            if name not in funcs:
                errors.append(
                    f"casino_commands.py 缺少 {name}()"
                )

        declared = []

        for node in ast.walk(commands):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue

            for decor in node.decorator_list:
                if not isinstance(decor, ast.Call):
                    continue

                if not (
                    isinstance(decor.func, ast.Attribute)
                    and decor.func.attr == "command"
                ):
                    continue

                for kw in decor.keywords:
                    if (
                        kw.arg == "name"
                        and isinstance(kw.value, ast.Constant)
                    ):
                        declared.append(kw.value.value)

        if declared.count("娛樂城") != 1:
            errors.append(
                "正式 /娛樂城 指令必須恰好一個；"
                f"目前 {declared.count('娛樂城')} 個"
            )

        if "娛樂城排行" in declared:
            errors.append(
                "多出頂層 /娛樂城排行；"
                "應移至 /娛樂城 面板"
            )

        if "casino_table_service" not in ast.unparse(
            commands
        ):
            warnings.append(
                "未偵測到二十一點／賓果服務接線"
            )

    main = ROOT / "main.py"

    if not main.is_file():
        errors.append("找不到 main.py")
    else:
        source = main.read_text(encoding="utf-8")

        if "casino_" not in source:
            warnings.append(
                "main.py 未明確處理 casino_ "
                "服務模組載入"
            )

    for item in errors:
        print("❌", item)

    for item in warnings:
        print("⚠️", item)

    print(
        f"檢查完成：{len(errors)} 個錯誤，"
        f"{len(warnings)} 個提醒"
    )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(audit())
