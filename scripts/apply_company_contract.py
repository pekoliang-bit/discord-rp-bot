
"""將員工合約安全檢查接入既有辭職函式。

執行：
python scripts/apply_company_contract.py

修改前保留備份。
不連接資料庫。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "modules" / "company_workforce.py"
BACKUP = TARGET.with_suffix(".py.contract_backup")


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    required = (
        ROOT / "core" / "company_contract_service.py",
        ROOT / "core" / "company_contract_store.py",
        ROOT / "core" / "company_contract_rules.py",
    )

    for file in required:
        if not file.is_file():
            raise FileNotFoundError(file)

    original = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(original)
    lines = original.splitlines(keepends=True)

    nodes = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "resign"
    ]

    if len(nodes) != 1:
        raise RuntimeError(
            "無法找到唯一的 resign()，已停止修改"
        )

    node = nodes[0]
    start = node.lineno - 1
    end = node.end_lineno

    function = "".join(lines[start:end])

    if "guard_resignation(" in function:
        print("離職合約檢查已經接入。")
        return

    anchor = "    with transaction() as db:\n"

    if function.count(anchor) != 1:
        raise RuntimeError(
            "辭職交易結構與預期不同，停止修改"
        )

    guard = '''
        from core.company_contract_service import (
            guard_resignation,
            finish_resignation,
        )

        guard_resignation(
            db, gid, company, cid
        )
'''

    function = function.replace(
        anchor,
        anchor + guard,
        1,
    )

    # 在員工狀態成功更新之後，與其餘原本
    # 的工作、升遷紀錄一起結束合約。
    error_anchor = '''        if not updated.rowcount:
            raise ValueError(
                "目前並未在該公司任職。"
            )
'''

    if function.count(error_anchor) != 1:
        raise RuntimeError(
            "找不到辭職成功的檢查位置，停止修改"
        )

    finish = '''
        finish_resignation(
            db, gid, company, cid
        )
'''

    function = function.replace(
        error_anchor,
        error_anchor + finish,
        1,
    )

    updated = (
        "".join(lines[:start])
        + function
        + "".join(lines[end:])
    )

    compile(updated, str(TARGET), "exec")

    if not BACKUP.exists():
        BACKUP.write_text(
            original, encoding="utf-8"
        )

    TARGET.write_text(
        updated, encoding="utf-8"
    )

    print("✅ 員工合約已接入辭職流程。")
    print(f"備份：{BACKUP.name}")


if __name__ == "__main__":
    main()
