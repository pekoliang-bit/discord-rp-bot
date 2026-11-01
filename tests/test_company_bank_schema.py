
import sqlite3
import unittest

from core.company_bank_opening import (
    initialize_company_bank,
)


class CompanyBankSchemaTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        initialize_company_bank(self.db)

    def test_required_fields(self):
        columns = {
            row["name"]
            for row in self.db.execute(
                "PRAGMA table_info("
                "rp_company_bank_accounts)"
            )
        }

        self.assertTrue({
            "guild_id",
            "company_id",
            "account_id",
            "initial_deposit",
            "opened_at",
            "status",
            "application_id",
        }.issubset(columns))

    def test_no_initial_money(self):
        self.db.execute("""
            INSERT INTO rp_company_bank_accounts
            (guild_id,company_id,account_id,
             opened_by,application_id)
            VALUES ('g','c',1,'u',10)
        """)

        row = self.db.execute("""
            SELECT initial_deposit
            FROM rp_company_bank_accounts
        """).fetchone()

        self.assertEqual(row["initial_deposit"], 0)

    def test_duplicate_company_rejected(self):
        self.db.execute("""
            INSERT INTO rp_company_bank_accounts
            (guild_id,company_id,account_id,
             opened_by,application_id)
            VALUES ('g','c',1,'u',10)
        """)

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.db.execute("""
                INSERT INTO rp_company_bank_accounts
                (guild_id,company_id,account_id,
                 opened_by,application_id)
                VALUES ('g','c',2,'u',11)
            """)

    def test_duplicate_application_rejected(self):
        self.db.execute("""
            INSERT INTO rp_company_bank_accounts
            (guild_id,company_id,account_id,
             opened_by,application_id)
            VALUES ('g','c',1,'u',10)
        """)

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.db.execute("""
                INSERT INTO rp_company_bank_accounts
                (guild_id,company_id,account_id,
                 opened_by,application_id)
                VALUES ('g','d',2,'u',10)
            """)


if __name__ == "__main__":
    unittest.main()
