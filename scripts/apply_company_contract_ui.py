
"""將員工合約入口加入原有 /公司 面板。

執行：
python scripts/apply_company_contract_ui.py
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "modules" / "company_portal.py"
BACKUP = TARGET.with_suffix(".py.contract_ui_backup")

OLD = """        if is_owner or can_review:
            owner_actions = []
"""

NEW = """        # 員工合約統一入口，所有玩家可查看本人合約。
        from modules.company_contract_management_ui import (
            CompanyContractEntry,
        )
        self.add_item(CompanyContractEntry())

        if is_owner or can_review:
            owner_actions = []
"""


def main():
    if not TARGET.exists():
        raise FileNotFoundError(TARGET)

    required = (
        ROOT / "modules" / "company_contract_management_ui.py",
        ROOT / "core" / "company_contract_offer_read.py",
        ROOT / "core" / "company_contract_review.py",
        ROOT / "core" / "company_contract_service.py",
        ROOT / "core" / "company_contract_store.py",
        ROOT / "core" / "company_contract_rules.py",
    )

    for path in required:
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少必要檔案：{path}"
            )

    original = TARGET.read_text(encoding="utf-8")

    if NEW in original:
        print("員工合約入口已經接入。")
        return

    if original.count(OLD) != 1:
        raise RuntimeError(
            "找不到預期的 CompanyPanel 插入位置；"
            "未修改任何檔案。"
        )

    updated = original.replace(OLD, NEW, 1)
    ast.parse(updated)
    compile(updated, str(TARGET), "exec")

    if not BACKUP.exists():
        BACKUP.write_text(
            original,
            encoding="utf-8",
        )

    TARGET.write_text(
        updated,
        encoding="utf-8",
    )
    print("✅ 已更新 modules/company_portal.py")


if __name__ == "__main__":
    main()
