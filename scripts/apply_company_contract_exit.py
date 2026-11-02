
"""將解約管理入口接入員工合約介面。

使用：
python scripts/apply_company_contract_exit.py
"""

from __future__ import annotations

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT / "modules/company_contract_management_ui.py"
)

BACKUP = TARGET.with_suffix(
    ".py.exit_backup"
)

MARKER = """        self.add_item(ContractListButton())"""

REPLACEMENT = """        self.add_item(ContractListButton())

        from core.company_contract_exit_service import (
            initialize as initialize_exits,
        )
        initialize_exits()
        # 解約介面必須先完成實際付款確認驗收。
        # 正式開放前暫不加入按鈕。
"""


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    source = TARGET.read_text(encoding="utf-8")

    if REPLACEMENT in source:
        print("已完成解約模組預備接線。")
        return

    if source.count(MARKER) != 1:
        raise RuntimeError(
            "合約面板版本不符，已停止修改"
        )

    updated = source.replace(
        MARKER, REPLACEMENT, 1
    )
    ast.parse(updated)

    if not BACKUP.exists():
        BACKUP.write_text(
            source, encoding="utf-8"
        )

    TARGET.write_text(
        updated, encoding="utf-8"
    )
    print("✅ 已加入解約模組初始化。")
    print("⚠️ 正式扣款介面維持關閉。")


if __name__ == "__main__":
    main()
