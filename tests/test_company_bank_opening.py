
"""公司銀行帳戶資料結構測試。"""

import sqlite3
import unittest

from core.company_bank_opening import (
    initialize_company_bank,
)


class CompanyBankTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        initialize_company_bank(self.db)

    def test_table_exists(self):
        row = self.db.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
              AND name='rp_company_bank_accounts'
        """).fetchone()

        self.assertIsNotNone(row)

    def test_duplicate_company_blocked(self):
        self.db.execute("""
            INSERT INTO rp_company_bank_accounts
            (guild_id, company_id, account_id,
             opened_by, application_id)
            VALUES ('1','A',10,'100',1)
        """)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO rp_company_bank_accounts
                (guild_id, company_id, account_id,
                 opened_by, application_id)
                VALUES ('1','A',11,'100',2)
            """)

    def test_duplicate_account_blocked(self):
        self.db.execute("""
            INSERT INTO rp_company_bank_accounts
            (guild_id, company_id, account_id,
             opened_by, application_id)
            VALUES ('1','A',10,'100',1)
        """)

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO rp_company_bank_accounts
                (guild_id, company_id, account_id,
                 opened_by, application_id)
                VALUES ('1','B',10,'100',2)
            """)

    def test_different_guilds(self):
        for guild in ("1", "2"):
            self.db.execute("""
                INSERT INTO rp_company_bank_accounts
                (guild_id, company_id, account_id,
                 opened_by, application_id)
                VALUES (?, 'A', 10, '100', 1)
            """, (guild,))

        count = self.db.execute("""
            SELECT COUNT(*) AS n
            FROM rp_company_bank_accounts
        """).fetchone()["n"]

        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
