"""場所行動：原子交易、重試、防未授權、未核准及回滾。"""
import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core import database
from core.action_execution import execute_action, ActionExecutionError


class TestLocationActionExecution(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'test.sqlite'
        patcher=patch.object(database, 'DATABASE_PATH', self.db)
        patcher.start();self.addCleanup(patcher.stop)
        database.init_db()
        with database.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO guilds (guild_id) VALUES ('g')")
            conn.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('g','u','測試角色','active')")
            self.cid=conn.execute('SELECT id FROM characters').fetchone()['id']
            conn.execute('''CREATE TABLE paid_action_packs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
               quantity INTEGER NOT NULL,remaining INTEGER NOT NULL,
               purchased_at TEXT NOT NULL, expires_at TEXT NOT NULL, request_key TEXT NOT NULL,
               UNIQUE(guild_id,request_key))''')
            now=datetime.now(timezone.utc)
            conn.execute('''INSERT INTO paid_action_packs(guild_id,character_id,quantity,remaining,purchased_at,expires_at,request_key)
             VALUES (?,?,?,?,?,?,?)''',('g',self.cid,5,5,now.isoformat(),(now+timedelta(days=3)).isoformat(),'pack1'))
            wallet=database.get_or_create_account(conn,'g','character',self.cid,'wallet')
            reserve=database.get_or_create_account(conn,'g','system','test-seed','treasury')
            from core.economy import _payment
            _payment(conn,'g','seed',reserve,wallet,20000,'test')

    def work(self):
        return {'name':'測試打工','category':'打工','approved':True,
                'cost_coins':0,'reward_coins':2500,'action_points_cost':1,
                'attribute':None,'attribute_increase':0,'point_rewards':{'自由點數':2}}

    def balance(self):
        with database.get_connection() as c:
            wallet=c.execute('''SELECT id FROM accounts WHERE guild_id='g' AND owner_type='character'
              AND owner_id=? AND account_type='wallet' ''',(str(self.cid),)).fetchone()['id']
            return database.account_balance(c,'g',wallet)

    def remaining(self):
        with database.get_connection() as c:
            return c.execute('SELECT SUM(remaining) AS n FROM paid_action_packs').fetchone()['n']

    def test_work_and_retry(self):
        old=self.balance()
        a=execute_action('g','u',self.cid,'room',self.work(),'work-1')
        self.assertEqual(a['reward_coins'],2500)
        self.assertEqual(self.balance(),old+2500)
        same=execute_action('g','u',self.cid,'room',self.work(),'work-1')
        self.assertTrue(same['duplicated'])
        self.assertEqual(self.balance(),old+2500)
        with database.get_connection() as c:
            self.assertEqual(c.execute('SELECT remaining FROM paid_action_packs').fetchone()['remaining'],4)
            self.assertEqual(c.execute("SELECT balance FROM lb_point_balances WHERE pool='free'").fetchone()['balance'],2)

    def test_not_approved(self):
        a=self.work();a['approved']=False
        with self.assertRaises(ActionExecutionError):execute_action('g','u',self.cid,'room',a,'no-1')

    def test_not_owner(self):
        with self.assertRaises(PermissionError):execute_action('g','other',self.cid,'room',self.work(),'bad-1')

    def test_insufficient_paid_ap_rolls_back(self):
        old=self.balance()
        with database.transaction() as c:
            c.execute('UPDATE paid_action_packs SET remaining=0')
        with self.assertRaises(ActionExecutionError):execute_action('g','u',self.cid,'room',self.work(),'bad-2')
        self.assertEqual(self.balance(),old)

    def test_invalid_health_rolls_back_paid_ap(self):
        a=self.work();a['health_delta']=-5
        old=self.balance()
        with self.assertRaises(ActionExecutionError):execute_action('g','u',self.cid,'room',a,'health-1')
        self.assertEqual(self.balance(),old)
        with database.get_connection() as c:
            self.assertEqual(c.execute('SELECT remaining FROM paid_action_packs').fetchone()['remaining'],5)

    def test_course_insufficient_coins_rolls_back(self):
        a={'name':'昂貴課程','category':'課程','approved':True,'cost_coins':999999,
           'reward_coins':0,'action_points_cost':0,'attribute':'容貌','attribute_increase':3}
        before=self.balance()
        with self.assertRaises(Exception):
            execute_action('g','u',self.cid,'training',a,'course-expensive')
        self.assertEqual(self.balance(),before)
        with database.get_connection() as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='rp_location_action_events'").fetchone():
                self.assertIsNone(c.execute("SELECT 1 FROM rp_location_action_events WHERE request_key='course-expensive'").fetchone())
            self.assertIsNone(c.execute("SELECT 1 FROM attributes WHERE name='容貌'").fetchone())

    def test_professional_points_separately_recorded(self):
        a=self.work();a['point_rewards']={'專業點數':3}
        execute_action('g','u',self.cid,'room',a,'professional-1')
        with database.get_connection() as c:
            r=c.execute('SELECT balance FROM rp_action_professional_points').fetchone()
            self.assertEqual(r['balance'],3)

    def test_request_collision_blocked(self):
        execute_action('g','u',self.cid,'room',self.work(),'same-1')
        a=self.work();a['name']='另一份打工'
        with self.assertRaises(ActionExecutionError):execute_action('g','u',self.cid,'room',a,'same-1')

    def test_paid_course_cost_and_skill(self):
        a={'name':'歌唱課','category':'課程','approved':True,'cost_coins':1000,
           'reward_coins':0,'action_points_cost':0,'attribute':'歌唱','attribute_increase':2}
        old=self.balance()
        execute_action('g','u',self.cid,'class',a,'course-1')
        self.assertEqual(self.balance(),old-1000)
        with database.get_connection() as c:
            r=c.execute("SELECT skill_value FROM character_skills WHERE skill_name='歌唱'").fetchone()
            self.assertEqual(r['skill_value'],2)

if __name__ == '__main__':unittest.main()
