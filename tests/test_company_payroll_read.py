
"""公司薪資查詢測試。"""

import sqlite3
import unittest

from unittest.mock import patch

from core import company_payroll_read as mod


class PayrollReadTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.executescript("""
            CREATE TABLE rp_company_payroll_settings (
                guild_id TEXT,
                business_id TEXT,
                cycle_days INTEGER,
                updated_at INTEGER
            );

            CREATE TABLE rp_company_payroll (
                guild_id TEXT,
                business_id TEXT,
                character_id INTEGER,
                pay_period TEXT,
                amount INTEGER,
                paid_at INTEGER
            );
        """)

    def query(self, allowed=True):
        from core import company_payroll_service
        from modules import company_treasury

        with (
            patch.object(
                company_payroll_service,
                "initialize_payroll",
            ),
            patch.object(
                mod,
                "get_connection",
                return_value=self.db,
            ),
            patch.object(
                company_treasury,
                "_finance_permission",
                return_value=allowed,
            ),
        ):
            return mod.payroll_cycle_summary(
                "g", "c", "u"
            )

    def test_unconfigured(self):
        text = self.query()
        self.assertIn("尚未設定", text)

    def test_seven_day_cycle(self):
        self.db.execute("""
            INSERT INTO rp_company_payroll_settings
            VALUES ('g','c',7,100)
        """)

        text = self.query()
        self.assertIn("每 7 天", text)

    def test_fourteen_day_cycle(self):
        self.db.execute("""
            INSERT INTO rp_company_payroll_settings
            VALUES ('g','c',14,100)
        """)

        text = self.query()
        self.assertIn("每 14 天", text)

    def test_history(self):
        self.db.execute("""
            INSERT INTO rp_company_payroll
            VALUES (
                'g','c',5,'7d:test:1',
                1000,100
            )
        """)

        text = self.query()
        self.assertIn("角色 #5", text)
        self.assertIn("1,000 金幣", text)

    def test_permission(self):
        with self.assertRaises(PermissionError):
            self.query(allowed=False)


if __name__ == "__main__":
    unittest.main()
