
"""將公司舊月薪函式接上7／14天發薪服務。

執行：
python scripts/apply_company_payroll.py

修改前自動建立備份，不操作資料庫。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = ROOT / "modules" / "company_treasury.py"

BACKUP = TARGET.with_suffix(
    ".py.payroll_backup"
)

NEW_FUNCTION = '''
def pay_employee_salary(
    guild_id,
    business_id,
    actor_id,
    character_id,
):
    """正式7／14天發薪；沿用原本函式入口。"""
    from core.company_payroll_service import pay_salary

    return pay_salary(
        guild_id,
        business_id,
        actor_id,
        character_id,
    )
'''


def main():
    if not TARGET.exists():
        raise FileNotFoundError(TARGET)

    service = (
        ROOT
        / "core"
        / "company_payroll_service.py"
    )

    if not service.exists():
        raise FileNotFoundError(
            "請先新增 company_payroll_service.py"
        )

    original = TARGET.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(original)

    matches = [
        node for node in tree.body
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.name == "pay_employee_salary"
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "找不到唯一的 pay_employee_salary 函式，"
            "停止修改以免破壞其他功能。"
        )

    node = matches[0]
    lines = original.splitlines(keepends=True)

    old_function = "".join(
        lines[node.lineno - 1:node.end_lineno]
    )

    if (
        "from core.company_payroll_service "
        "import pay_salary"
        in old_function
    ):
        print("已經接入新版發薪服務。")
        return

    updated = (
        "".join(lines[:node.lineno - 1])
        + NEW_FUNCTION.strip("\n")
        + "\n"
        + "".join(lines[node.end_lineno:])
    )

    compile(
        updated,
        str(TARGET),
        "exec",
    )

    if not BACKUP.exists():
        BACKUP.write_text(
            original,
            encoding="utf-8",
        )

    TARGET.write_text(
        updated,
        encoding="utf-8",
    )

    print(
        "✅ 已更新 modules/company_treasury.py"
    )
    print(f"備份：{BACKUP.name}")
    print(
        "注意：公司持有人須設定發薪週期；"
        "舊月薪紀錄不會自動轉換。"
    )


if __name__ == "__main__":
    main()
