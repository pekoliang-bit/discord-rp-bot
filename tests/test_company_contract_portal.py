
"""公司主面板與員工合約入口整合檢查。"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "modules" / "company_portal.py"


class CompanyContractPortalTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = PORTAL.read_text(
            encoding="utf-8"
        )
        cls.tree = ast.parse(cls.source)

    def test_portal_syntax(self):
        self.assertIsNotNone(self.tree)

    def test_contract_button_connected(self):
        self.assertIn(
            "CompanyContractEntry",
            self.source,
            "請先執行 apply_company_contract_ui.py",
        )

    def test_existing_employee_actions_kept(self):
        for name in (
            "ApplyModal",
            "ResignModal",
            "WorkModal",
            "PositionModal",
        ):
            self.assertIn(name, self.source)

    def test_existing_company_command_kept(self):
        self.assertIn(
            'name="公司"',
            self.source,
        )

    def test_owner_manager_functions_kept(self):
        for name in (
            "InviteManagerModal",
            "ManagerPermissionsModal",
        ):
            self.assertIn(name, self.source)


if __name__ == "__main__":
    unittest.main()
