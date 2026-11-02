
"""公司員工合約 SQLite 測試。"""

import sqlite3
import unittest

from core.company_contract_store import (
    initialize_contracts,
    active_contract,
)
from core.company_contract_service import (
    guard_resignation,
    finish_resignation,
)


class ContractServiceTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        initialize_contracts(self.db)

    def add_contract(self, penalty=0):
        self.db.execute("""
            INSERT INTO rp_company_employee_contracts
            (
                guild_id,company_id,character_id,
                employee_user_id,employer_user_id,
                salary,cycle_days,penalty,
                employee_accepted,employer_accepted,
                status
            )
            VALUES (
                'g','company1',5,
                'employee','employer',
                5000,7,?,1,1,'active'
            )
        """, (penalty,))

    def test_active_contract(self):
        self.add_contract()

        result = active_contract(
            self.db, "g", "company1", 5
        )

        self.assertIsNotNone(result)

    def test_penalty_blocks_resignation(self):
        self.add_contract(10000)

        with self.assertRaises(PermissionError):
            guard_resignation(
                self.db, "g", "company1", 5
            )

    def test_zero_penalty_allows_resignation(self):
        self.add_contract(0)

        guard_resignation(
            self.db, "g", "company1", 5
        )

        finish_resignation(
            self.db, "g", "company1", 5
        )

        row = active_contract(
            self.db, "g", "company1", 5
        )

        self.assertIsNone(row)

    def test_no_contract_allows_legacy_resign(self):
        guard_resignation(
            self.db, "g", "company1", 5
        )

    def test_no_duplicate_active_contract(self):
        self.add_contract()

        with self.assertRaises(
            sqlite3.IntegrityError
        ):
            self.add_contract()


if __name__ == "__main__":
    unittest.main()
