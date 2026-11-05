"""集團公司歸屬及隔離權限：僅使用記憶體資料庫。"""
import sqlite3
import unittest
from core.company_group_membership import attach_all_owned, attach_company


class CompanyGroupMembershipGuardTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute("""CREATE TABLE rp_business_entities (
            guild_id TEXT, business_id TEXT, kind TEXT,
            owner_id TEXT, status TEXT, parent_id TEXT
        )""")

    def add(self, bid, kind, owner="100", guild="1", parent=None):
        self.db.execute("INSERT INTO rp_business_entities VALUES (?,?,?,?,?,?)",
                        (guild, bid, kind, owner, "active", parent))

    def test_owner_attaches_only_own_unassigned_companies(self):
        self.add("G1", "group")
        self.add("C1", "company")
        self.add("C2", "company", "200")
        self.add("B1", "brand")
        self.add("C3", "company", parent="OTHER")
        self.assertEqual(attach_all_owned(self.db, "1", "G1", "100"), 1)
        rows = self.db.execute("SELECT business_id FROM rp_business_entities WHERE parent_id='G1'").fetchall()
        self.assertEqual([r[0] for r in rows], ["C1"])
        self.assertEqual(attach_all_owned(self.db, "1", "G1", "100"), 0)

    def test_non_owner_rejected_even_when_no_companies(self):
        self.add("G1", "group")
        with self.assertRaises(PermissionError):
            attach_all_owned(self.db, "1", "G1", "200")

    def test_cross_guild_group_rejected(self):
        self.add("G1", "group", guild="2")
        self.add("C1", "company")
        with self.assertRaises(LookupError):
            attach_company(self.db, "1", "G1", "C1", "100")

    def test_already_attached_other_group_rejected(self):
        self.add("G1", "group")
        self.add("C1", "company", parent="G2")
        with self.assertRaises(ValueError):
            attach_company(self.db, "1", "G1", "C1", "100")


if __name__ == "__main__":
    unittest.main()
