
"""五間公司成立集團的正式規則測試。"""

import sqlite3
import unittest

from core.company_group_eligibility import (
    eligible_companies,
    validate_group_application,
    attach_owned_companies,
)


class CompanyGroupTests(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

        self.db.execute("""
            CREATE TABLE rp_business_entities (
                guild_id TEXT,
                business_id TEXT,
                name TEXT,
                kind TEXT,
                owner_id TEXT,
                status TEXT,
                founded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                parent_id TEXT
            )
        """)

    def add(self, name, kind, owner="100"):
        self.db.execute("""
            INSERT INTO rp_business_entities
            (guild_id, business_id, name, kind,
             owner_id, status)
            VALUES ('1', ?, ?, ?, ?, 'active')
        """, (name, name, kind, owner))

    def test_brand_not_counted(self):
        for i in range(10):
            self.add(f"B{i}", "brand")

        self.assertEqual(
            len(eligible_companies(
                self.db, "1", "100"
            )),
            0,
        )

    def test_five_companies_required(self):
        for i in range(4):
            self.add(f"C{i}", "company")

        with self.assertRaises(ValueError):
            validate_group_application(
                self.db, "1", "100"
            )

        self.add("C4", "company")

        result = validate_group_application(self.db, "1", "100")
        self.assertEqual(result["company_count"], 5)
        self.assertTrue(result["automatic_eligible"])

    def test_only_own_companies(self):
        for i in range(5):
            self.add(f"Other{i}", "company", "200")

        self.assertEqual(
            eligible_companies(
                self.db, "1", "100"
            ),
            (),
        )

    def test_direct_group_application_without_five_companies(self):
        result = validate_group_application(
            self.db, "1", "100", method="direct"
        )
        self.assertTrue(result["direct_eligible"])

    def test_existing_group_blocks_duplicate(self):
        self.add("G1", "group")
        with self.assertRaises(ValueError):
            validate_group_application(self.db, "1", "100", method="direct")

    def test_parented_company_not_eligible_for_auto_group(self):
        for i in range(5):
            self.add(f"C{i}", "company")
        self.db.execute("UPDATE rp_business_entities SET parent_id='OTHER' WHERE business_id='C0'")
        with self.assertRaises(ValueError):
            validate_group_application(self.db, "1", "100")

    def test_group_attachment(self):
        for i in range(5):
            self.add(f"C{i}", "company")

        self.add("G1", "group")

        changed = attach_owned_companies(
            self.db, "1", "100", "G1"
        )

        self.assertEqual(changed, 5)

        count = self.db.execute("""
            SELECT COUNT(*)
            FROM rp_business_entities
            WHERE kind='company'
              AND parent_id='G1'
        """).fetchone()[0]

        self.assertEqual(count, 5)


if __name__ == "__main__":
    unittest.main()
