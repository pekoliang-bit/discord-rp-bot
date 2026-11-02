
"""公司解約案件 SQLite 儲存測試。"""

import sqlite3
import unittest

from core.company_contract_exit_store import (
    initialize_exit_store,
    get_exit,
)


class ExitStoreTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        initialize_exit_store(self.db)

    def insert(self, contract=10, key="exit:10"):
        return self.db.execute("""
            INSERT INTO rp_company_contract_exits
            (
                guild_id,contract_id,company_id,
                character_id,requested_by,reason,
                payment_key
            )
            VALUES ('g',?,'c',5,'u','ordinary',?)
        """, (contract, key)).lastrowid

    def test_create(self):
        eid = self.insert()
        self.assertEqual(
            get_exit(self.db, "g", eid)["status"],
            "pending",
        )

    def test_duplicate_pending_blocked(self):
        self.insert()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(key="another")

    def test_payment_key_unique(self):
        self.insert()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert(contract=11, key="exit:10")

    def test_completed_unique(self):
        eid = self.insert()
        self.db.execute("""
            UPDATE rp_company_contract_exits
            SET status='completed'
            WHERE id=?
        """, (eid,))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO rp_company_contract_exits
                (
                    guild_id,contract_id,company_id,
                    character_id,requested_by,reason,
                    payment_key,status
                )
                VALUES
                ('g',10,'c',5,'u','ordinary',
                 'second','completed')
            """)


if __name__ == "__main__":
    unittest.main()
