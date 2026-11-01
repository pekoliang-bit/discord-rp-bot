"""彩票隔離測試；只使用本機暫存 SQLite，不接觸正式資料庫。"""
import os, sys, tempfile, unittest, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
_TMP=tempfile.TemporaryDirectory(prefix='casino_lottery_check_')
os.environ['DATABASE_PATH']=str(Path(_TMP.name)/'lottery.sqlite3')
from core.database import init_db,register_guild,create_character,transaction,get_or_create_account,account_balance
from core.economy import _payment
from modules.casino_repository import init_casino_schema,upsert_game
from modules.casino_lottery import init_lottery_schema,create_draw,buy_lottery,prize_probabilities
from modules.casino_service import CasinoError
from datetime import datetime,timedelta,timezone
GID='910001'; UID='910002'
class LotteryFlowTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  init_db();init_casino_schema();init_lottery_schema();register_guild(GID)
  cls.cid=create_character(GID,UID,'彩票測試角色',26)
  with transaction() as conn:
   conn.execute("UPDATE characters SET status='active' WHERE id=?",(cls.cid,))
   issuer=get_or_create_account(conn,GID,'system','seed','treasury');wallet=get_or_create_account(conn,GID,'character',str(cls.cid),'wallet')
   _payment(conn,GID,'seed:lottery',issuer,wallet,100000,'測試金幣')
  upsert_game(guild_id=GID,code='test_lotto',name='測試彩票',kind='lottery',ticket_price=100,enabled=True,settings={'number_pool':20,'pick_count':4,'prizes':{'4':10000,'3':1000,'2':200},'max_single_reward':10000,'rules_verified':True,'rollover':False,'daily_purchase_limit':20})
  now=datetime.now(timezone.utc);cls.draw=create_draw(guild_id=GID,game_code='test_lotto',draw_code='test-draw',cutoff_at=(now+timedelta(days=1)).isoformat(),draw_at=(now+timedelta(days=2)).isoformat())
 def balance(self):
  with transaction() as conn:
   aid=get_or_create_account(conn,GID,'character',str(self.cid),'wallet');return account_balance(conn,GID,aid)
 def test_probabilities_sum_one(self):
  probs=prize_probabilities(number_pool=30,pick_count=6)
  self.assertAlmostEqual(sum(probs.values()),1.0)
  self.assertAlmostEqual(probs[6],1/math.comb(30,6))
 def test_purchase_dedup_and_balance(self):
  before=self.balance();kwargs=dict(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_lotto',draw_id=self.draw['id'],request_key='lotto:one',numbers=[1,2,3,4])
  a=buy_lottery(**kwargs);b=buy_lottery(**kwargs)
  self.assertEqual(a['round_id'],b['round_id']);self.assertTrue(b['duplicate']);self.assertEqual(self.balance(),before-100)
 def test_change_numbers_rejected(self):
  kwargs=dict(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_lotto',draw_id=self.draw['id'],request_key='lotto:changed',numbers=[1,2,3,4])
  buy_lottery(**kwargs)
  with self.assertRaises(CasinoError):buy_lottery(**(kwargs | {'numbers':[1,2,3,5]}))
 def test_invalid_numbers_dont_charge(self):
  before=self.balance()
  with self.assertRaises(CasinoError):buy_lottery(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_lotto',draw_id=self.draw['id'],request_key='lotto:bad',numbers=[1,1,2,3])
  self.assertEqual(self.balance(),before)
if __name__=='__main__':unittest.main(verbosity=2)
