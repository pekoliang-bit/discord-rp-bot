
"""將薪資週期管理按鈕接入現有公司金庫介面。

使用：
    python scripts/apply_payroll_ui.py

不操作資料庫。
修改前建立備份。
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT
    / "modules"
    / "company_treasury_ui.py"
)

BACKUP = TARGET.with_suffix(
    ".py.payroll_ui_backup"
)

OLD = '''        # 只有持有人看到管理層額度設定按鈕。
        if self.is_owner:
            self.add_item(
                OwnerSpendingLimitButton()
            )
'''

NEW = '''        # 只有持有人看到管理層額度設定按鈕。
        if self.is_owner:
            self.add_item(
                OwnerSpendingLimitButton()
            )

        from modules.company_payroll_ui import (
            PayrollCycleButton,
            PayrollCycleSummaryButton,
        )

        if self.is_owner:
            self.add_item(PayrollCycleButton())

        self.add_item(PayrollCycleSummaryButton())
'''


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    required = (
        ROOT / "modules" / "company_payroll_ui.py",
        ROOT / "core" / "company_payroll_read.py",
        ROOT / "core" / "company_payroll_service.py",
    )

    for path in required:
        if not path.is_file():
            raise FileNotFoundError(
                f"缺少必要檔案：{path}"
            )

    original = TARGET.read_text(encoding="utf-8")

    # Newer versions already expose a safe cycle-setting button;
    # duplicate controls would confuse players and may exceed View limits.
    if "self.add_item(PayrollCycleSummaryButton())" in original:
        print("薪資管理介面已接入（設定及查閱），無需重複修改。")
        return

    if NEW in original:
        print("薪資管理介面已經接入，無需重複修改。")
        return

    if original.count(OLD) != 1:
        raise RuntimeError(
            "找不到預期的金庫面板插入位置。"
            "原始碼版本可能不同，已停止修改。"
        )

    updated = original.replace(OLD, NEW, 1)

    updated = updated.replace(
        '"核發本月員工薪資"',
        '"核發員工薪資"',
        1,
    )

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

    print("✅ 已接入公司薪資管理介面。")
    print(f"修改檔案：{TARGET}")
    print(f"備份檔案：{BACKUP}")


if __name__ == "__main__":
    main()
