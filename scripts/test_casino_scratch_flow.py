"""刮刮樂完整開獎測試，使用獨立暫存 SQLite，不接觸正式資料。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
_TMP=tempfile.TemporaryDirectory(prefix='casino_scratch_test_')
os.environ['DATABASE_PATH']=str(Path(_TMP.name)/'scratch.sqlite3')
from core.database import init_db,register_guild,create_character,transaction,get_or_create_account,account_balance
from core.economy import _payment
from modules.casino_repository import init_casino_schema,upsert_game
from modules.casino_scratch import scratch_ticket,validate_scratch
from modules.casino_service import CasinoError
G='830001';U='830002'

class ScratchTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  init_db();init_casino_schema();register_guild(G)
  cls.cid=create_character(G,U,'測試刮刮樂',25)
  with transaction() as conn:
   conn.execute("UPDATE characters SET status='active' WHERE id=?",(cls.cid,))
   source=get_or_create_account(conn,G,'system','scratch_seed','treasury')
   target=get_or_create_account(conn,G,'character',str(cls.cid),'wallet')
   _payment(conn,G,'scratch:seed',source,target,10000)
  cls.settings={'rules_verified':True,'model':'independent','daily_purchase_limit':20,'max_single_reward':1000,'prizes':[
   {'code':'no_prize','name':'銘謝惠顧','coins':0,'weight':800000},
   {'code':'small','name':'小獎','coins':100,'weight':150000},
   {'code':'jackpot','name':'頭獎','coins':1000,'weight':50000}]}
  cls.game=upsert_game(guild_id=G,code='scratch_case',name='測試票',kind='scratch',ticket_price=100,settings=cls.settings,enabled=True)
 def balance(self):
  with transaction() as conn:
   aid=get_or_create_account(conn,G,'character',str(self.cid),'wallet');return account_balance(conn,G,aid)
 def test_01_rtp_and_no_prize(self):
  config=validate_scratch(self.game)
  self.assertEqual(config['rtp_ppm'],650000)
  self.assertTrue(any(p['code']=='no_prize' for p in config['prizes']))
 def test_02_end_to_end_replay(self):
  before=self.balance()
  x=scratch_ticket(guild_id=G,player_id=U,character_id=self.cid,game_code='scratch_case',request_key='scratch:replay')
  after=self.balance()
  y=scratch_ticket(guild_id=G,player_id=U,character_id=self.cid,game_code='scratch_case',request_key='scratch:replay')
  self.assertEqual(x['id'],y['id']);self.assertEqual(x['outcome_json'],y['outcome_json']);self.assertEqual(after,self.balance())
  self.assertEqual(after,before-100+x['reward_coins'])
  self.assertEqual(x['status'],'settled')
 def test_03_invalid_probability(self):
  broken=dict(self.game)
  import json
  cfg=dict(self.settings);cfg['prizes']=[dict(x) for x in self.settings['prizes']];cfg['prizes'][0]['weight']=700000
  broken['settings_json']=json.dumps(cfg)
  with self.assertRaises(CasinoError):validate_scratch(broken)

if __name__=='__main__': unittest.main(verbosity=2)
