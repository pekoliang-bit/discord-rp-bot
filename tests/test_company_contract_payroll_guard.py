
"""合約生效前的薪資週期檢查。"""

import sqlite3
import unittest

from core.company_contract_payroll_guard import (
    validate_contract_activation,
)


class ContractActivationTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.execute("""
            CREATE TABLE rp_company_payroll_settings(
                guild_id TEXT,
                business_id TEXT,
                cycle_days INTEGER
            )
        """)

    def configure(self, days):
        self.db.execute(
            """
            INSERT INTO rp_company_payroll_settings
            VALUES ('g','c',?)
            """,
            (days,),
        )

    def test_seven_days(self):
        self.configure(7)
        self.assertTrue(
            validate_contract_activation(
                self.db, "g", "c", 7
            )
        )

    def test_fourteen_days(self):
        self.configure(14)
        self.assertTrue(
            validate_contract_activation(
                self.db, "g", "c", 14
            )
        )

    def test_missing_setting(self):
        with self.assertRaises(ValueError):
            validate_contract_activation(
                self.db, "g", "c", 7
            )

    def test_mismatch(self):
        self.configure(7)
        with self.assertRaises(ValueError):
            validate_contract_activation(
                self.db, "g", "c", 14
            )


if __name__ == "__main__":
    unittest.main()
