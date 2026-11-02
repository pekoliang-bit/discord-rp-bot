
"""正式合約薪資核對測試。"""

import sqlite3
import unittest

from core.company_contract_payroll_link import (
    resolve_payroll_terms,
)


class ContractPayrollLinkTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.executescript("""
            CREATE TABLE characters(
                guild_id TEXT,
                id INTEGER,
                owner_id TEXT,
                status TEXT
            );

            CREATE TABLE rp_company_employee_contracts(
                id INTEGER PRIMARY KEY,
                guild_id TEXT,
                company_id TEXT,
                character_id INTEGER,
                employee_user_id TEXT,
                salary INTEGER,
                cycle_days INTEGER,
                employer_accepted INTEGER,
                employee_accepted INTEGER,
                status TEXT
            );

            INSERT INTO characters
            VALUES ('g',5,'u','active');
        """)

    def add_contract(
        self,
        salary=8000,
        cycle=7,
        owner="u",
    ):
        self.db.execute("""
            INSERT INTO rp_company_employee_contracts
            (
                guild_id,company_id,character_id,
                employee_user_id,salary,cycle_days,
                employer_accepted,employee_accepted,status
            )
            VALUES ('g','c',5,?,?,?,1,1,'active')
        """, (owner, salary, cycle))

    def resolve(self):
        return resolve_payroll_terms(
            self.db, "g", "c", 5, 5000, 7
        )

    def test_legacy_salary(self):
        terms = self.resolve()
        self.assertEqual(terms.salary, 5000)
        self.assertIsNone(terms.contract_id)

    def test_contract_salary(self):
        self.add_contract()
        terms = self.resolve()
        self.assertEqual(terms.salary, 8000)
        self.assertEqual(
            terms.source,
            "active_contract",
        )

    def test_cycle_mismatch(self):
        self.add_contract(cycle=14)
        with self.assertRaises(ValueError):
            self.resolve()

    def test_owner_mismatch(self):
        self.add_contract(owner="other")
        with self.assertRaises(PermissionError):
            self.resolve()

    def test_zero_salary_rejected(self):
        self.add_contract(salary=0)
        with self.assertRaises(ValueError):
            self.resolve()

    def test_duplicate_active_contract(self):
        self.add_contract()
        self.add_contract()
        with self.assertRaises(RuntimeError):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
