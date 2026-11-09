"""店鋪銀行帳戶核准時的權限、冪等與回滾測試。"""
import sqlite3
import unittest

from core.shop_bank_opening import approve_shop_bank


class ShopBankOpeningTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        for ddl in (
            'CREATE TABLE guilds (guild_id TEXT PRIMARY KEY)',
            '''CREATE TABLE accounts (id INTEGER PRIMARY KEY, guild_id TEXT NOT NULL,
                owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, account_type TEXT NOT NULL,
                UNIQUE(guild_id,owner_type,owner_id,account_type))''',
            '''CREATE TABLE characters (guild_id TEXT,id INTEGER,owner_id TEXT,status TEXT,
                PRIMARY KEY(guild_id,id))''',
            '''CREATE TABLE personal_shops (guild_id TEXT,id INTEGER,character_id INTEGER,status TEXT,
                PRIMARY KEY(guild_id,id))''',
        ):
            self.db.execute(ddl)
        self.db.execute("INSERT INTO characters VALUES('g1',17,'u1','active')")
        self.db.execute("INSERT INTO personal_shops VALUES('g1',41,17,'open')")

    def tearDown(self):
        self.db.close()

    def test_open_and_idempotent_replay(self):
        account = approve_shop_bank(self.db, 'g1', 41, 'u1', 17, 501)
        self.assertGreater(account, 0)
        self.assertEqual(approve_shop_bank(self.db, 'g1', 41, 'u1', 17, 501), account)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM rp_personal_shop_bank_accounts').fetchone()[0], 1)

    def test_rejects_different_application(self):
        approve_shop_bank(self.db, 'g1', 41, 'u1', 17, 501)
        with self.assertRaises(ValueError):
            approve_shop_bank(self.db, 'g1', 41, 'u1', 17, 502)

    def test_rejects_wrong_owner_and_other_guild(self):
        with self.assertRaises(PermissionError):
            approve_shop_bank(self.db, 'g1', 41, 'u2', 17, 501)
        with self.assertRaises(ValueError):
            approve_shop_bank(self.db, 'g2', 41, 'u1', 17, 501)

    def test_rejects_closed_shop(self):
        self.db.execute("UPDATE personal_shops SET status='closed' WHERE guild_id='g1'")
        with self.assertRaises(ValueError):
            approve_shop_bank(self.db, 'g1', 41, 'u1', 17, 501)


if __name__ == '__main__':
    unittest.main()
