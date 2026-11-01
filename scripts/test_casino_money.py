"""離線娛樂城金流回歸測試：建立暫存資料庫，不接觸正式玩家資料。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.TemporaryDirectory(prefix='casino_test_')
os.environ['DATABASE_PATH'] = str(Path(_TMP.name) / 'test.sqlite3')

from core.database import init_db, register_guild, create_character, transaction, get_or_create_account, account_balance
from core.economy import _payment
from modules.casino_repository import init_casino_schema, upsert_game
from modules.casino_service import purchase_round, settle_round, CasinoError

GID = '810001'
UID = '810002'

class MoneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db(); init_casino_schema(); register_guild(GID)
        cls.cid = create_character(GID, UID, '驗收角色', 23)
        cls.young = create_character(GID, UID, '未滿年齡角色', 19)
        with transaction() as conn:
            conn.execute("UPDATE characters SET status='active' WHERE guild_id=?", (GID,))
            issuer = get_or_create_account(conn,GID,'system','test_seed','treasury')
            wallet = get_or_create_account(conn,GID,'character',str(cls.cid),'wallet')
            _payment(conn,GID,'test:seed',issuer,wallet,100000,'測試環境金幣')
        upsert_game(guild_id=GID,code='audit_scratch',name='金流驗收票',kind='scratch',ticket_price=100,
                    settings={'rules_verified':True,'daily_purchase_limit':20,'max_single_reward':1000},enabled=True)
    def balance(self):
        with transaction() as conn:
            aid=get_or_create_account(conn,GID,'character',str(self.cid),'wallet')
            return account_balance(conn,GID,aid)
    def test_01_purchase_and_duplicate(self):
        before=self.balance()
        a=purchase_round(guild_id=GID,player_id=UID,character_id=self.cid,game_code='audit_scratch',request_key='audit:purchase')
        b=purchase_round(guild_id=GID,player_id=UID,character_id=self.cid,game_code='audit_scratch',request_key='audit:purchase')
        self.assertEqual(a['id'],b['id']);self.assertEqual(self.balance(),before-100)
    def test_02_reward_and_duplicate(self):
        row=purchase_round(guild_id=GID,player_id=UID,character_id=self.cid,game_code='audit_scratch',request_key='audit:reward')
        before=self.balance()
        first=settle_round(guild_id=GID,round_id=row['id'],outcome={'prize':'test'},reward_coins=500,expected_kind='scratch')
        second=settle_round(guild_id=GID,round_id=row['id'],outcome={'prize':'test'},reward_coins=500,expected_kind='scratch')
        self.assertEqual(first['reward_tx_id'],second['reward_tx_id']);self.assertEqual(self.balance(),before+500)
    def test_03_changing_result_rejected(self):
        with self.assertRaises(CasinoError):
            settle_round(guild_id=GID,round_id=self._id('audit:reward'),outcome={'prize':'changed'},reward_coins=800)
    def _id(self,key):
        with transaction() as conn:
            return conn.execute('SELECT id FROM casino_rounds WHERE guild_id=? AND request_key=?',(GID,key)).fetchone()['id']
    def test_04_underage_rejected(self):
        with self.assertRaises(CasinoError):
            purchase_round(guild_id=GID,player_id=UID,character_id=self.young,game_code='audit_scratch',request_key='audit:young')
    def test_05_wrong_owner_rejected(self):
        with self.assertRaises(CasinoError):
            purchase_round(guild_id=GID,player_id='different',character_id=self.cid,game_code='audit_scratch',request_key='audit:owner')
    def test_06_over_limit_reward_rejected(self):
        row=purchase_round(guild_id=GID,player_id=UID,character_id=self.cid,game_code='audit_scratch',request_key='audit:limit')
        with self.assertRaises(CasinoError):
            settle_round(guild_id=GID,round_id=row['id'],outcome={'prize':'oversize'},reward_coins=1001)
        with transaction() as conn:
            st=conn.execute('SELECT status,reward_tx_id FROM casino_rounds WHERE id=?',(row['id'],)).fetchone()
        self.assertEqual(st['status'],'pending');self.assertIsNone(st['reward_tx_id'])

if __name__=='__main__': unittest.main(verbosity=2)
