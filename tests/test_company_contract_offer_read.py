
"""員工合約查詢測試。"""

import sqlite3
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from core import company_contract_offer_read as service
from core.company_contract_store import initialize_contracts


class ContractReadTests(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "contracts.db")

        with sqlite3.connect(self.path) as db:
            initialize_contracts(db)
            db.execute("""
                INSERT INTO rp_company_employee_contracts
                (
                    guild_id,company_id,character_id,
                    employee_user_id,employer_user_id,
                    salary,cycle_days,penalty,
                    status
                )
                VALUES
                ('g','c',1,'employee','owner',
                 5000,7,0,'pending'),
                ('g','c',2,'other','owner',
                 5000,7,0,'pending')
            """)

        def connection():
            db = sqlite3.connect(self.path)
            db.row_factory = sqlite3.Row
            return db

        self.connection = connection

    def test_employee_only_sees_own_contract(self):
        with patch.object(
            service,
            "get_connection",
            side_effect=self.connection,
        ):
            rows = service.contract_list(
                "g",
                "employee",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["character_id"], 1
        )

    def test_owner_sees_offered_contracts(self):
        with patch.object(
            service,
            "get_connection",
            side_effect=self.connection,
        ):
            rows = service.contract_list(
                "g",
                "owner",
            )

        self.assertEqual(len(rows), 2)

    def test_unrelated_user_sees_nothing(self):
        with patch.object(
            service,
            "get_connection",
            side_effect=self.connection,
        ):
            rows = service.contract_list(
                "g",
                "stranger",
            )

        self.assertEqual(rows, [])

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            service.contract_list(
                "g", "employee", limit=0
            )


if __name__ == "__main__":
    unittest.main()
