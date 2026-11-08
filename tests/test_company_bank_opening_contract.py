import sqlite3
import sys
import types
import unittest

from unittest.mock import patch, MagicMock

from core import company_bank_opening as mod


class CompanyBankOpeningTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.execute("""
            CREATE TABLE rp_business_entities (
                guild_id TEXT,
                business_id TEXT,
                kind TEXT,
                status TEXT
            )
        """)

        self.db.execute("""
            INSERT INTO rp_business_entities
            VALUES ('g','c','company','active')
        """)

        mod.initialize_company_bank(self.db)

        # The production validator lives in a Discord UI module.  Patch the
        # lazy import during isolated DB tests, without mocking banking SQL.
        mocked = types.ModuleType('modules.application_extras')
        mocked.validate_company_bank_applicant = MagicMock(return_value=None)
        authorization = patch.dict(sys.modules, {'modules.application_extras': mocked})
        authorization.start()
        self.addCleanup(authorization.stop)

    def open(self, application_id=7):
        with patch.object(
            mod,
            "get_or_create_account",
            return_value=99,
        ):
            return mod.approve_company_bank(
                self.db,
                "g",
                "c",
                "u",
                application_id,
            )

    def test_open_account(self):
        account_id = self.open()

        self.assertEqual(account_id, 99)

    def test_same_application_retry(self):
        first = self.open(7)
        second = self.open(7)

        self.assertEqual(first, second)

        count = self.db.execute("""
            SELECT COUNT(*)
            FROM rp_company_bank_accounts
        """).fetchone()[0]

        self.assertEqual(count, 1)

    def test_different_application_rejected(self):
        self.open(7)

        with self.assertRaises(ValueError):
            self.open(8)

    def test_inactive_company_rejected(self):
        self.db.execute("""
            UPDATE rp_business_entities
            SET status='inactive'
        """)

        with self.assertRaises(ValueError):
            self.open()

    def test_brand_cannot_open_company_account(self):
        self.db.execute("""
            UPDATE rp_business_entities
            SET kind='brand'
        """)

        with self.assertRaises(ValueError):
            self.open()


if __name__ == "__main__":
    unittest.main()
