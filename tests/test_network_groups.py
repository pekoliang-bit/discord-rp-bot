"""玩家網路帳號、邀請與群組資料層回歸測試；不連線 Discord。"""
import os
import tempfile
import unittest

class TestNetworkGroups(unittest.TestCase):
    def setUp(self):
        from core import config
        self.tmp=tempfile.TemporaryDirectory()
        self.old=config.DATABASE_PATH
        config.DATABASE_PATH=os.path.join(self.tmp.name,'network.sqlite3')
        # core.database 於匯入時捕捉設定路徑；必須同時替換。
        from core import database
        self.old_db=database.DATABASE_PATH
        database.DATABASE_PATH=config.DATABASE_PATH
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('77','test')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('77','100','Alice','active')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('77','200','Bob','active')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('77','200','Other','draft')")
    def tearDown(self):
        from core import database,config
        database.DATABASE_PATH=self.old_db; config.DATABASE_PATH=self.old; self.tmp.cleanup()
    def test_flow(self):
        from core import network_groups as n
        a=n.create_account(77,100,1,'alice'); b=n.create_account(77,200,2,'bob')
        self.assertEqual(len(n.accounts(77,100,1)),1)
        with self.assertRaises(PermissionError): n.accounts(77,200,1)
        with self.assertRaises(PermissionError): n.create_account(77,200,3,'draft_handle')
        gid=n.begin_group(77,100,a,'test group'); n.activate_group(77,gid,456)
        self.assertEqual(n.invite(77,100,a,gid,'Bob #2')['id'],b)
        self.assertEqual(n.memberships(77,200,b)[0]['state'],'invited')
        self.assertEqual(n.accept(77,200,b,gid)['channel_id'],'456')
        self.assertEqual(n.memberships(77,200,b)[0]['state'],'joined')
        self.assertFalse(n.leave(77,200,b,gid)['still_has_access'])
        with self.assertRaises(PermissionError): n.invite(77,200,b,gid,'@alice')
        with self.assertRaises(ValueError): n.create_account(77,100,1,'bob')
        with self.assertRaises(ValueError): n.leave(77,100,a,gid)
    def test_multiple_accounts_same_owner(self):
        from core import network_groups as n
        a=n.create_account(77,100,1,'alice'); b=n.create_account(77,100,1,'alice2')
        group=n.begin_group(77,100,a,'group'); n.activate_group(77,group,456)
        n.invite(77,100,a,group,'@alice2');n.accept(77,100,b,group)
        self.assertTrue(n.leave(77,100,b,group)['still_has_access'])
    def test_default_config(self):
        from core import network_groups as n
        self.assertEqual(n.settings(n.DEFAULT_GUILD)['category_id'],n.DEFAULT_CATEGORY)
        self.assertIsNone(n.settings(77)['category_id'])

if __name__=='__main__': unittest.main()
