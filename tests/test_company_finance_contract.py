
"""公司銀行、金庫與發薪的整合契約檢查。

此測試核對程式碼的基本整合要求，
不會執行正式金幣交易。
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CompanyFinanceContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.treasury = (
            ROOT / "modules" / "company_treasury.py"
        ).read_text(encoding="utf-8")

        cls.applications = (
            ROOT / "modules" / "application_extras.py"
        ).read_text(encoding="utf-8")

        cls.opening = (
            ROOT / "core" / "company_bank_opening.py"
        ).read_text(encoding="utf-8")

    def test_shared_ledger(self):
        self.assertIn(
            "get_or_create_account",
            self.opening,
        )

        self.assertIn(
            "account_balance",
            self.treasury,
        )

    def test_payroll_uses_company_bank(self):
        self.assertIn(
            "def pay_employee_salary(",
            self.treasury,
        )

        self.assertIn(
            "rp_company_bank_accounts",
            self.treasury,
        )

    def test_deposit_withdraw_exist(self):
        self.assertIn(
            "def move_company_bank(",
            self.treasury,
        )

        self.assertIn(
            '"deposit"',
            self.treasury,
        )

        self.assertIn(
            '"withdraw"',
            self.treasury,
        )

    def test_authorization_service_exists(self):
        self.assertIn(
            "def validate_company_bank_applicant(",
            self.applications,
        )

    def test_bank_account_has_status(self):
        self.assertIn(
            "initial_deposit",
            self.opening,
        )

        self.assertIn(
            "frozen",
            self.opening,
        )


if __name__ == "__main__":
    unittest.main()
