
"""將公司商務管理加入既有 /公司 面板。

執行：
python scripts/apply_company_commercial.py

只修改 Python 原始碼，不修改資料庫。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "modules/company_portal.py"

BACKUP = TARGET.with_suffix(
    ".py.commercial_backup"
)

REQUIRED = (
    "core/company_career_progress.py",
    "core/company_endorsement_rules.py",
    "core/company_endorsement_store.py",
    "core/company_endorsement_service.py",
    "core/company_vip_service.py",
    "core/company_brand_exposure.py",
    "modules/company_commercial_ui.py",
)

INSERT = """
        from modules.company_commercial_ui import (
            CompanyCommercialEntry,
        )
        self.add_item(CompanyCommercialEntry())

"""


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    for relative in REQUIRED:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少檔案：{relative}"
            )

    source = TARGET.read_text(
        encoding="utf-8"
    )

    if "self.add_item(CompanyCommercialEntry())" in source:
        print("商務管理入口已經存在。")
        return

    tree = ast.parse(source)

    panel = next(
        (
            node for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "CompanyPanel"
        ),
        None,
    )

    if panel is None:
        raise RuntimeError(
            "找不到 CompanyPanel，"
            "沒有修改原始碼。"
        )

    initializer = next(
        (
            node for node in panel.body
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name == "__init__"
        ),
        None,
    )

    if initializer is None:
        raise RuntimeError(
            "找不到公司面板初始化函式"
        )

    lines = source.splitlines(
        keepends=True
    )

    # 使用現有面板尾端的插入位置。
    # 若沒有固定標記則停止，不修改其它方法。
    marker = (
        "        if is_owner or can_review:\n"
    )

    matches = [
        index
        for index in range(
            initializer.lineno - 1,
            initializer.end_lineno,
        )
        if lines[index] == marker
    ]

    if len(matches) != 1:
        raise RuntimeError(
            "公司面板版本與預期不同，"
            "無法安全加入商務入口。"
        )

    pos = matches[0]

    updated = (
        "".join(lines[:pos])
        + INSERT
        + "".join(lines[pos:])
    )

    ast.parse(updated)
    compile(
        updated,
        str(TARGET),
        "exec",
    )

    if not BACKUP.exists():
        BACKUP.write_text(
            source,
            encoding="utf-8",
        )

    TARGET.write_text(
        updated,
        encoding="utf-8",
    )

    print(
        "✅ 已加入 /公司 → 商務管理"
    )
    print(
        "原始碼備份：",
        BACKUP.name,
    )
    print(
        "請確認 Discord View "
        "沒有超過25個元件或單列5個元件。"
    )


if __name__ == "__main__":
    main()
