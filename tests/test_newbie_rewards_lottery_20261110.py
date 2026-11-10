"""新手獎勵／普通抽獎券／普通獎池交易隔離測試；不連 Discord。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core.economy import _payment


class NewbieRewardsPoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_patch=patch.object(database,'DATABASE_PATH',str(Path(self.tmp.name)/'test.db'))
        db_patch.start();self.addCleanup(db_patch.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("CREATE TABLE shop_config(guild_id TEXT PRIMARY KEY,capacity INTEGER)")
            db.execute("""CREATE TABLE shop_products(id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT,name TEXT,category TEXT,description TEXT,price INTEGER,
                stock INTEGER,scope TEXT,effect_kind TEXT,active INTEGER,created_at TEXT,updated_at TEXT)""")
            db.execute("""CREATE TABLE shop_inventory(guild_id TEXT,character_id INTEGER,product_id INTEGER,
                quantity INTEGER NOT NULL, PRIMARY KEY(guild_id,character_id,product_id))""")
        from modules.task_repository import initialize
        initialize()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id) VALUES('g') ON CONFLICT DO NOTHING")
            for cid,uid in ((10,'u1'),(11,'u1'),(20,'u2')):
                db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status,character_type) VALUES(?,?,?,?,?,'player')",
                    (cid,'g',uid,f'Player{cid}','active'))
                src=database.get_or_create_account(db,'g','system','test_funding','wallet')
                dst=database.get_or_create_account(db,'g','character',str(cid),'wallet')
                _payment(db,'g',f'test:fund:{cid}',src,dst,50000,'測試資金')
            db.execute("""INSERT INTO shop_products
                (guild_id,name,category,description,price,stock,scope,effect_kind,active,created_at,updated_at)
                VALUES ('g','測試玩偶','B09','',1000,100,'public','none',1,'now','now')""")
            db.execute("INSERT INTO schools(guild_id,name) VALUES('g','測試學校')")
            db.execute("INSERT INTO courses(guild_id,title,subject,fee,status) VALUES('g','表演訓練','戲劇',800,'open')")

    def test_ticket_purchase_weekly_limit_and_idempotence(self):
        from core.ordinary_lottery_tickets import purchase,balance,configure
        configure('g','owner')
        for n in range(5):
            self.assertTrue(purchase('g','u1',10,f'purchase-{n}'))
        self.assertFalse(purchase('g','u1',10,'purchase-0'))
        self.assertEqual(balance('g',10),5)
        with self.assertRaises(ValueError):purchase('g','u1',11,'sixth')
        self.assertTrue(purchase('g','u2',20,'first-u2'))
        with self.assertRaises(ValueError):purchase('g','u1',11,'purchase-0',2)

    def test_pool_draw_once_and_no_double_spend(self):
        from core.ordinary_lottery_tickets import grant_newbie,balance
        from core.ordinary_lottery_pool import draw
        grant_newbie('g','u1',10,'test1')
        record,fresh=draw('g','u1',10,'draw1')
        self.assertTrue(fresh)
        self.assertIn(record['prize_type'],('product','course'))
        self.assertEqual(balance('g',10),0)
        again,new=draw('g','u1',10,'draw1')
        self.assertFalse(new)
        self.assertEqual(again['id'] if 'id' in again else again['request_key'],
                         record['id'] if 'id' in record else record['request_key'])
        with self.assertRaises(ValueError):draw('g','u1',10,'draw2')

    def test_points_and_coins_no_double_issue(self):
        from modules.task_repository import record_event
        from modules.task_newbie_rewards import deliver
        from modules.leaderboard_points_storage import get_balance
        record_event('g',10,'newbie:查看','command:查看')
        record_event('g',10,'newbie:公司','command:公司')
        self.assertEqual(deliver('g',10,'u1','查看'),'done')
        self.assertEqual(deliver('g',10,'u1','查看'),'done')
        self.assertEqual(get_balance('g',10)['free'],30)
        self.assertEqual(deliver('g',10,'u1','公司'),'done')
        self.assertEqual(deliver('g',10,'u1','公司'),'done')
        with database.get_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE guild_id='g' AND unique_key LIKE 'newbie:%'").fetchone()[0],1)

    def test_shop_credit_single_spend(self):
        from core.newbie_shop_credit import grant,balance,spend_in_transaction
        self.assertTrue(grant('g',10,1500,'newbie-story'))
        self.assertFalse(grant('g',10,1500,'newbie-story'))
        with database.transaction() as db:
            self.assertEqual(spend_in_transaction(db,'g',10,1000,'order1'),1000)
        self.assertEqual(balance('g',10),500)
        with database.transaction() as db:
            self.assertEqual(spend_in_transaction(db,'g',10,1000,'order1'),1000)
        self.assertEqual(balance('g',10),500)

if __name__=='__main__':unittest.main()
