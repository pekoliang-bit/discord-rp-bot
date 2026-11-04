"""任務獎勵隔離 SQLite 回歸測試；不動正式玩家資料。

執行：python scripts/test_task_rewards_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory(prefix='rp_task_test_')
os.environ['DATABASE_PATH']=str(Path(TMP.name)/'test.sqlite3')

from core.database import init_db,transaction,register_guild,get_connection, get_or_create_account,account_balance
from modules.task_repository import initialize,publish_task,accept_task
from modules.task_rewards import settle_reward
from modules.task_service import character_info,eligible


class RewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db();register_guild('888','隔離測試');initialize()
        with transaction() as db:
            db.execute("INSERT INTO characters(guild_id,owner_id,name,age,occupation,status) VALUES('888','777','測試角色',22,'演員','active')")
            db.execute('CREATE TABLE rp_character_status(guild_id TEXT,character_id INTEGER,level INTEGER)')
            db.execute("INSERT INTO rp_character_status VALUES('888',1,5)")
            db.execute('CREATE TABLE shop_config(guild_id TEXT PRIMARY KEY,capacity INTEGER)')
            db.execute('CREATE TABLE shop_products(id INTEGER PRIMARY KEY,guild_id TEXT,name TEXT)')
            db.execute('CREATE TABLE shop_inventory(guild_id TEXT,character_id INTEGER,product_id INTEGER,quantity INTEGER,PRIMARY KEY(guild_id,character_id,product_id))')
            db.execute("INSERT INTO shop_products VALUES(30,'888','測試獎品')")

    def make_task(self,code,rewards):
        publish_task('888',code,'daily',code,rewards=rewards,status='published')
        with get_connection() as db:
            task_id=db.execute('SELECT id FROM rp_task_definitions WHERE guild_id=? AND code=?',('888',code)).fetchone()['id']
        self.assertTrue(accept_task('888',1,task_id))
        with transaction() as db:
            db.execute("UPDATE rp_task_progress SET status='completed' WHERE guild_id='888' AND task_id=?",(task_id,))
        return task_id

    def test_01_cafe_uses_real_status(self):
        char=character_info('888',1,'777')
        self.assertEqual(char['cafe_level'],5)
        self.assertTrue(eligible('888',{'kind':'daily','requirements_json':'{"min_cafe_level":5}'},char)['eligible'])
        self.assertFalse(eligible('888',{'kind':'daily','requirements_json':'{"min_cafe_level":6}'},char)['eligible'])

    def test_02_money_and_stats_retry(self):
        tid=self.make_task('money_stats',{'coins':150,'attributes':{'演繹':2}})
        self.assertTrue(settle_reward('888',1,tid)['claimed'])
        self.assertTrue(settle_reward('888',1,tid)['claimed'])
        with get_connection() as db:
            acc=get_or_create_account(db,'888','character','1','wallet')
            self.assertEqual(account_balance(db,'888',acc),150)
            self.assertEqual(db.execute("SELECT value FROM attributes WHERE guild_id='888' AND character_id=1 AND name='演繹'").fetchone()[0],2)

    def test_03_item_retry(self):
        tid=self.make_task('item',{'items':[{'item_id':30,'quantity':3}]})
        self.assertTrue(settle_reward('888',1,tid)['claimed'])
        self.assertTrue(settle_reward('888',1,tid)['claimed'])
        with get_connection() as db:
            self.assertEqual(db.execute('SELECT quantity FROM shop_inventory WHERE product_id=30').fetchone()[0],3)

    def test_04_missing_item_is_not_claimed(self):
        tid=self.make_task('missing',{'items':[{'item_id':999,'quantity':1}]})
        result=settle_reward('888',1,tid)
        self.assertFalse(result['claimed'])
        with get_connection() as db:
            self.assertEqual(db.execute('SELECT status FROM rp_task_progress WHERE task_id=?',(tid,)).fetchone()[0],'completed')

    def test_05_unapproved_cannot_pay(self):
        publish_task('888','unapproved','daily','unapproved',rewards={'coins':999},status='published')
        with get_connection() as db:
            tid=db.execute("SELECT id FROM rp_task_definitions WHERE code='unapproved'").fetchone()[0]
        accept_task('888',1,tid)
        with self.assertRaises(ValueError):settle_reward('888',1,tid)


if __name__=='__main__':
    try:unittest.main(verbosity=2)
    finally:TMP.cleanup()
