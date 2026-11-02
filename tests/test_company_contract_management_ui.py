
"""Discord 員工合約介面結構檢查。"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = (
    ROOT / "modules"
    / "company_contract_management_ui.py"
)


class ContractUITests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = TARGET.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_modal_exists(self):
        self.assertIn(
            "class ContractOfferModal",
            self.source,
        )

    def test_decision_modal_exists(self):
        self.assertIn(
            "class ContractDecisionModal",
            self.source,
        )

    def test_existing_services_used(self):
        for name in (
            "offer_contract",
            "accept_contract",
            "reject_contract",
            "contract_list",
        ):
            self.assertIn(name, self.source)

    def test_no_new_slash_commands(self):
        self.assertNotIn(
            "@app_commands.command",
            self.source,
        )
        self.assertNotIn(
            "app_commands.Group(",
            self.source,
        )

    def test_panel_button_exists(self):
        self.assertIn(
            "class CompanyContractEntry",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
