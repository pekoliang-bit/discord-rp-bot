"""V5.0 新人禮包離線交易測試：不使用正式資料庫、不啟用線上抽獎。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import sys
import core.database as database
from modules import newcomer_lottery as nl


class NewcomerLotteryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = database.DATABASE_PATH
        database.DATABASE_PATH = str(Path(self.temp.name)/'test.db')
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds (guild_id) VALUES ('1')")
            for name in ('a','b','c','d','e'):
                db.execute("INSERT INTO characters (guild_id,owner_id,name,status) VALUES ('1','7',?,'active')", (name,))

    def tearDown(self):
        database.DATABASE_PATH = self.path
        self.temp.cleanup()

    def test_disabled_and_foreign_character(self):
        with self.assertRaisesRegex(ValueError, '尚未正式開放'):
            nl.draw('1','7',1)
        nl.set_enabled('1', True)
        with self.assertRaisesRegex(ValueError, '本人'):
            nl.draw('1','8',1)

    def test_all_prize_types_and_once_per_character(self):
        nl.set_enabled('1',True)
        for cid, kind in enumerate(['money','free','professional','skill','money'],1):
            with patch.object(nl, '_select_reward', return_value=(kind,5000 if kind=='money' else 15,None)):
                out = nl.draw('1','7',cid)
                self.assertEqual(out['kind'],kind)
            again=nl.draw('1','7',cid)
            self.assertEqual(again['status'],'already')
        with database.get_connection() as db:
            rows=db.execute('SELECT pool,balance FROM lb_point_balances ORDER BY pool').fetchall()
            self.assertEqual([(x['pool'],x['balance']) for x in rows],[('free',15),('skill',15)])
            self.assertEqual(db.execute('SELECT balance FROM rp_action_professional_points').fetchone()['balance'],15)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_newcomer_lottery_draws WHERE state="done"').fetchone()[0],5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE unique_key LIKE 'newcomer:v5:%'").fetchone()[0],2)

    def test_product_fallback_when_catalog_missing(self):
        with database.transaction() as db:
            nl.initialize(db)
            with patch.object(nl.secrets,'randbelow', return_value=99):
                kind,amount,product_id=nl._select_reward(db,'1')
        self.assertEqual((kind,amount,product_id),('money',5000,None))

    def test_product_grant_does_not_reduce_shop_stock(self):
        nl.set_enabled('1', True)
        with database.transaction() as db:
            nl.initialize(db)
            db.execute("""CREATE TABLE shop_products (
                id INTEGER PRIMARY KEY, guild_id TEXT, name TEXT, category TEXT,
                description TEXT, price INTEGER, scope TEXT, active INTEGER, stock INTEGER)""")
            db.execute("""CREATE TABLE shop_inventory (
                guild_id TEXT,character_id INTEGER,product_id INTEGER,quantity INTEGER,
                PRIMARY KEY(guild_id,character_id,product_id))""")
            db.execute("INSERT INTO shop_products VALUES (9,'1','普通背包','配件','',5000,'public',1,0)")
        def adjust_inventory(db, gid, cid, product, delta):
            db.execute("""INSERT INTO shop_inventory VALUES(?,?,?,?)
                ON CONFLICT(guild_id,character_id,product_id)
                DO UPDATE SET quantity=quantity+excluded.quantity""", (gid,cid,product,delta))
        with patch.dict(sys.modules, {'modules.shop':SimpleNamespace(adjust_inventory=adjust_inventory)}):
            with patch.object(nl.secrets, 'randbelow', side_effect=[99,0]):
                out=nl.draw('1','7',1)
        self.assertEqual(out['product_id'],9)
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT stock FROM shop_products WHERE id=9').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT quantity FROM shop_inventory WHERE product_id=9').fetchone()[0],1)

    def test_rollback_and_retry_original_prize(self):
        nl.set_enabled('1',True)
        with patch.object(nl,'_select_reward',return_value=('product',1,900)):
            with self.assertRaises(Exception):
                nl.draw('1','7',1)
        pending = nl.history('1','7')
        self.assertEqual(pending[0]['state'],'pending')
        self.assertEqual(pending[0]['reward_kind'],'product')
        with patch.object(nl,'_select_reward',return_value=('money',5000,None)):
            with self.assertRaises(Exception):
                nl.draw('1','7',1)
        self.assertEqual(nl.history('1','7')[0]['reward_kind'],'product')


if __name__=='__main__': unittest.main()
