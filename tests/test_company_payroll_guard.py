
"""公司發薪設定安全測試。

使用暫存 SQLite，不操作正式玩家帳戶。
"""

import sqlite3
import unittest
from unittest.mock import patch

from core import company_payroll_service as service


class DBContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        return False


class PayrollGuardTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.executescript("""
            CREATE TABLE rp_company_payroll_settings (
                guild_id TEXT,
                business_id TEXT,
                cycle_days INTEGER,
                updated_by TEXT,
                updated_at INTEGER,
                PRIMARY KEY(guild_id,business_id)
            );

            CREATE TABLE rp_company_payroll (
                guild_id TEXT,
                business_id TEXT,
                character_id INTEGER,
                pay_period TEXT,
                amount INTEGER,
                paid_by TEXT,
                reference_key TEXT,
                paid_at INTEGER
            );
        """)

    def configure(self, days, is_owner=True):
        from modules import company_treasury

        with (
            patch.object(
                service,
                "initialize_payroll",
            ),
            patch.object(
                service,
                "transaction",
                return_value=DBContext(self.db),
            ),
            patch.object(
                company_treasury,
                "_is_owner",
                return_value=is_owner,
            ),
        ):
            return service.configure_payroll_cycle(
                "g",
                "company1",
                "owner",
                days,
            )

    def test_seven_days(self):
        self.configure(7)

        row = self.db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
        """).fetchone()

        self.assertEqual(row["cycle_days"], 7)

    def test_fourteen_days(self):
        self.configure(14)

        row = self.db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
        """).fetchone()

        self.assertEqual(row["cycle_days"], 14)

    def test_invalid_cycle(self):
        with self.assertRaises(ValueError):
            self.configure(30)

    def test_non_owner_rejected(self):
        with self.assertRaises(PermissionError):
            self.configure(7, is_owner=False)

    def test_change_without_payroll(self):
        self.configure(7)
        self.configure(14)

        row = self.db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
        """).fetchone()

        self.assertEqual(row["cycle_days"], 14)

    def test_change_after_payroll_rejected(self):
        self.configure(7)

        self.db.execute("""
            INSERT INTO rp_company_payroll
            VALUES (
                'g','company1',1,'7d:old:1',
                1000,'owner','payment1',100
            )
        """)

        self.db.commit()

        with self.assertRaises(ValueError):
            self.configure(14)

        row = self.db.execute("""
            SELECT cycle_days
            FROM rp_company_payroll_settings
        """).fetchone()

        self.assertEqual(row["cycle_days"], 7)


if __name__ == "__main__":
    unittest.main()
