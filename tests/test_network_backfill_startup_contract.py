import os
import tempfile
import unittest

class TestBackfillCompatibility(unittest.TestCase):
    def setUp(self):
        from core import config, database
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = config.DATABASE_PATH
        self.old_database = database.DATABASE_PATH
        config.DATABASE_PATH = database.DATABASE_PATH = os.path.join(self.tmp.name, 'test.db')
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('72','Test')")
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('73','Other')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('72','10','Alice','active')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('72','10','Draft','draft')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('73','11','Other','active')")
    def tearDown(self):
        from core import config, database
        config.DATABASE_PATH = self.old_config
        database.DATABASE_PATH = self.old_database
        self.tmp.cleanup()
    def test_missing_and_idempotent(self):
        from core.network_groups import missing_default_characters, ensure_default_accounts
        self.assertEqual(missing_default_characters(72), [1])
        a = ensure_default_accounts(72, 1)
        self.assertEqual({r['account_kind'] for r in a}, {'personal', 'official'})
        self.assertEqual(len(ensure_default_accounts(72, 1)), 2)
        self.assertEqual(missing_default_characters(72), [])
        self.assertEqual(missing_default_characters(73), [3])
        self.assertEqual(ensure_default_accounts(72, 2), [])
    def test_custom_account_kept(self):
        from core.network_groups import create_account, ensure_default_accounts
        from core.database import get_connection
        custom_id = create_account(72, 10, 1, 'customAlice')
        self.assertEqual(len(ensure_default_accounts(72, 1)), 2)
        with get_connection() as db:
            row = db.execute('SELECT handle,account_kind FROM rp_network_accounts WHERE id=?', (custom_id,)).fetchone()
        self.assertEqual((row['handle'],row['account_kind']), ('customAlice','custom'))
    def test_limits(self):
        from core.network_groups import missing_default_characters
        for limit in (0,-1,1001):
            with self.assertRaises(ValueError):missing_default_characters(72,limit)
if __name__ == '__main__': unittest.main()
