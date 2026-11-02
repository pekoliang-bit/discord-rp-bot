
"""公司合約與薪資服務的原始碼整合檢查。"""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "payroll": ROOT / "core/company_payroll_service.py",
    "contract": ROOT / "core/company_contract_service.py",
    "script": ROOT / "scripts/apply_contract_payroll_link.py",
}


class IntegrationTests(unittest.TestCase):

    def test_required_files(self):
        for path in FILES.values():
            self.assertTrue(
                path.is_file(),
                str(path),
            )

    def test_payroll_syntax(self):
        source = FILES["payroll"].read_text(
            encoding="utf-8"
        )
        self.assertIsNotNone(ast.parse(source))

    def test_contract_syntax(self):
        source = FILES["contract"].read_text(
            encoding="utf-8"
        )
        self.assertIsNotNone(ast.parse(source))

    def test_salary_link_present(self):
        source = FILES["payroll"].read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "resolve_payroll_terms(",
            source,
            "請先執行整合腳本",
        )

    def test_contract_guard_present(self):
        source = FILES["contract"].read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "validate_contract_activation(",
            source,
            "請先執行整合腳本",
        )


if __name__ == "__main__":
    unittest.main()
