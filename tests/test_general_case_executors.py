"""一般申請業務執行器的純 SQLite 測試，不依賴 Discord。"""
import sqlite3
import unittest

from core.general_case_executors import initialize_general_cases, execute_general_case


class GeneralCaseTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE characters (id INTEGER, guild_id TEXT, owner_id TEXT, status TEXT)')
        self.db.execute('''CREATE TABLE rp_general_applications (
            id INTEGER PRIMARY KEY, guild_id TEXT, user_id TEXT, character_id INTEGER,
            kind TEXT, details TEXT, status TEXT, reviewer_id TEXT, reviewed_at TEXT)''')
        self.db.executemany('INSERT INTO characters VALUES (?,?,?,?)',[(1,'g','u1','active'),(2,'g','u2','active')])
        initialize_general_cases(self.db)

    def tearDown(self):
        self.db.close()

    def _application(self, id, uid, cid, kind, details):
        self.db.execute('INSERT INTO rp_general_applications (id,guild_id,user_id,character_id,kind,details,status) VALUES (?,?,?,?,?,?,?)',
                        (id,'g',uid,cid,kind,details,'pending'))
        return self.db.execute('SELECT * FROM rp_general_applications WHERE id=?',(id,)).fetchone()

    def test_leave_records_real_period(self):
        r=self._application(1,'u1',1,'leave','【開始與結束日期（YYYY-MM-DD 至 YYYY-MM-DD）】2026-11-09 至 2026-11-16\n【請假事由與需要暫停的義務】暫停通告')
        execute_general_case(self.db,r,'staff')
        self.assertEqual(self.db.execute('SELECT start_date,end_date FROM rp_character_leaves').fetchone()['end_date'],'2026-11-16')

    def test_invalid_date_blocked(self):
        r=self._application(1,'u1',1,'leave','【開始與結束日期（YYYY-MM-DD 至 YYYY-MM-DD）】2026-02-31 至 2026-03-10\n【請假事由與需要暫停的義務】請假')
        with self.assertRaises(ValueError): execute_general_case(self.db,r,'staff')

    def test_marriage_requires_reciprocal_consent(self):
        a=self._application(1,'u1',1,'cp_register','【對方角色編號】2\n【關係類型（伴侶／婚姻）】婚姻')
        with self.assertRaises(ValueError): execute_general_case(self.db,a,'staff')
        self._application(2,'u2',2,'cp_register','【對方角色編號】1\n【關係類型（伴侶／婚姻）】婚姻')
        execute_general_case(self.db,a,'staff')
        self.assertEqual(self.db.execute('SELECT relationship_kind FROM rp_registered_partnerships').fetchone()[0],'婚姻')
        self.assertEqual(self.db.execute('SELECT status FROM rp_general_applications WHERE id=2').fetchone()[0],'approved')

    def test_cannot_review_own_partner(self):
        a=self._application(1,'u1',1,'cp_register','【對方角色編號】2\n【關係類型（伴侶／婚姻）】伴侶')
        self._application(2,'u2',2,'cp_register','【對方角色編號】1\n【關係類型（伴侶／婚姻）】伴侶')
        with self.assertRaises(PermissionError): execute_general_case(self.db,a,'u2')

    def test_no_multiple_partners(self):
        a=self._application(1,'u1',1,'cp_register','【對方角色編號】2\n【關係類型（伴侶／婚姻）】伴侶')
        self._application(2,'u2',2,'cp_register','【對方角色編號】1\n【關係類型（伴侶／婚姻）】伴侶')
        execute_general_case(self.db,a,'staff')
        with self.assertRaises(ValueError): execute_general_case(self.db,a,'staff')


if __name__=='__main__': unittest.main()
