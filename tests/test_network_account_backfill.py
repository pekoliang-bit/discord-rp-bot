"""多語言帳號與舊角色自動補建，不需 Discord 網路。"""
import json
import os
import tempfile
import unittest


class TestNetworkAccountBackfill(unittest.TestCase):
    def setUp(self):
        from core import config, database
        self.tmp=tempfile.TemporaryDirectory()
        self.old_config=config.DATABASE_PATH
        self.old_db=database.DATABASE_PATH
        config.DATABASE_PATH=database.DATABASE_PATH=os.path.join(self.tmp.name,'test.sqlite3')
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('99','test')")
            db.execute('''INSERT INTO characters(guild_id,owner_id,name,status,settings_json)
                VALUES(?,?,?,?,?)''',('99','100','林小雨','active',json.dumps({'stage_name':'小雨'})))

    def tearDown(self):
        from core import config, database
        config.DATABASE_PATH=self.old_config
        database.DATABASE_PATH=self.old_db
        self.tmp.cleanup()

    def test_auto_pair_idempotent(self):
        from core import network_groups as n
        self.assertEqual(n.missing_default_characters(99),[1])
        a=n.ensure_default_accounts(99,1)
        self.assertEqual({r['handle'] for r in a},{'林小雨','小雨Oficial'})
        self.assertEqual(len(n.ensure_default_accounts(99,1)),2)
        self.assertEqual(n.missing_default_characters(99),[])

    def test_unicode_and_safety(self):
        from core import network_groups as n
        n.ensure_default_accounts(99,1)
        self.assertTrue(n.create_account(99,100,1,'さくら🌸'))
        self.assertTrue(n.create_account(99,100,1,'Bonjour 世界'))
        with self.assertRaises(ValueError): n.create_account(99,100,1,'@everyone')
        with self.assertRaises(ValueError): n.create_account(99,100,1,'さくら🌸')

    def test_adopt_old_account(self):
        from core import network_groups as n
        n.create_account(99,100,1,'小雨')
        a=n.ensure_default_accounts(99,1)
        self.assertEqual(len(a),2)
        self.assertEqual(sum(r['account_kind']=='personal' for r in a),1)

    def test_name_collision(self):
        from core import network_groups as n
        from core.database import transaction
        with transaction() as db:
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('99','200','小雨','active')")
        a=n.ensure_default_accounts(99,1)
        b=n.ensure_default_accounts(99,2)
        self.assertEqual(len({r['handle'] for r in a+b}),4)

    def test_old_schema_migration(self):
        from core.database import transaction
        from core import network_groups as n
        with transaction() as db:
            db.execute('''CREATE TABLE rp_network_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
                character_id INTEGER NOT NULL,handle TEXT NOT NULL COLLATE NOCASE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id,handle),UNIQUE(guild_id,character_id,id))''')
            db.execute("INSERT INTO rp_network_accounts(guild_id,character_id,handle) VALUES('99',1,'olduser')")
        a=n.ensure_default_accounts(99,1)
        self.assertEqual(len(a),2)  # 只回傳私人／官方兩個自動帳號
        from core.database import get_connection
        with get_connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_network_accounts WHERE guild_id=? AND character_id=?', ('99',1)).fetchone()[0],3)
        self.assertIn('account_kind',a[0])


if __name__=='__main__': unittest.main()
