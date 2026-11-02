
"""薪資週期管理介面原始碼契約測試。"""

import ast
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

UI = ROOT / "modules" / "company_treasury_ui.py"

PATCH = ROOT / "scripts" / "apply_payroll_ui.py"

ADDON = ROOT / "modules" / "company_payroll_ui.py"


class PayrollUIPatchTests(unittest.TestCase):

    def test_files_exist(self):
        for path in (UI, PATCH, ADDON):
            self.assertTrue(
                path.is_file(),
                f"缺少 {path}",
            )

    def test_addon_syntax(self):
        source = ADDON.read_text(encoding="utf-8")
        ast.parse(source)

    def test_only_owner_can_configure(self):
        source = ADDON.read_text(encoding="utf-8")

        self.assertIn(
            'current["is_owner"]',
            source,
        )

        self.assertIn(
            "configure_payroll_cycle",
            source,
        )

    def test_ui_integration(self):
        source = UI.read_text(encoding="utf-8")

        self.assertIn(
            "PayrollCycleButton",
            source,
            "尚未執行 apply_payroll_ui.py",
        )

        self.assertIn(
            "PayrollCycleSummaryButton",
            source,
        )

    def test_old_functions_preserved(self):
        source = UI.read_text(encoding="utf-8")

        for function in (
            "CompanyBankMoveModal",
            "PayrollModal",
            "TreasuryTransferModal",
            "SpendingLimitModal",
            "TreasuryDetailView",
        ):
            self.assertIn(function, source)


if __name__ == "__main__":
    unittest.main()
