"""賭桌、抽抽樂隔離驗證；僅測安全金流及非法獎池拒絕。"""
import os,sys,tempfile,unittest,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
_TMP=tempfile.TemporaryDirectory(prefix='casino_wager_check_')
os.environ['DATABASE_PATH']=str(Path(_TMP.name)/'wager.sqlite3')
from core.database import init_db,register_guild,create_character,transaction,get_or_create_account,account_balance
from core.economy import _payment
from modules.casino_repository import init_casino_schema,upsert_game,get_game
from modules.casino_gacha import validate_gacha,draw_gacha
from modules.casino_wager_service import play_wager
from modules.casino_service import CasinoError
GID='920001';UID='920002'
class WagerGachaTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  init_db();init_casino_schema();register_guild(GID);cls.cid=create_character(GID,UID,'賭場測試角色',28)
  with transaction() as conn:
   conn.execute("UPDATE characters SET status='active' WHERE id=?",(cls.cid,))
   issuer=get_or_create_account(conn,GID,'system','seed','treasury');wallet=get_or_create_account(conn,GID,'character',str(cls.cid),'wallet');_payment(conn,GID,'seed:wager',issuer,wallet,100000,'測試金幣')
  upsert_game(guild_id=GID,code='test_roulette',name='測試輪盤',kind='roulette',ticket_price=100,enabled=True,settings={'rules_verified':True,'wager_enabled':True,'daily_purchase_limit':20,'max_single_reward':10000})
  upsert_game(guild_id=GID,code='test_gacha_invalid',name='錯誤獎池',kind='gacha',ticket_price=100,enabled=True,settings={'rules_verified':True,'pool_family':'test','rarity_weights':{'N':500000,'R':300000,'SR':150000,'SSR':40000,'UR':10000},'ssr_pity':50,'ur_pity':100,'prizes':[],'max_single_reward':10000})
 def balance(self):
  with transaction() as conn:
   aid=get_or_create_account(conn,GID,'character',str(self.cid),'wallet');return account_balance(conn,GID,aid)
 def test_roulette_request_is_idempotent(self):
  before=self.balance();args=dict(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_roulette',request_key='wager:red',choice='red',payment_source='wallet')
  a=play_wager(**args);mid=self.balance();b=play_wager(**args)
  self.assertEqual(a['id'],b['id']);self.assertEqual(self.balance(),mid);self.assertEqual(mid,before-100+a['reward_coins'])
 def test_roulette_cannot_change_same_bet(self):
  original=dict(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_roulette',request_key='wager:changed',choice='red')
  play_wager(**original)
  with self.assertRaises(CasinoError):play_wager(**(original | {'choice':'black'}))
 def test_invalid_wager_does_not_charge(self):
  before=self.balance()
  with self.assertRaises((CasinoError,ValueError)):play_wager(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_roulette',request_key='wager:invalid',choice='not-a-bet')
  self.assertEqual(self.balance(),before)
 def test_empty_gacha_pool_blocked_without_charge(self):
  before=self.balance()
  with self.assertRaises(CasinoError):draw_gacha(guild_id=GID,player_id=UID,character_id=self.cid,game_code='test_gacha_invalid',request_key='gacha:invalid')
  self.assertEqual(self.balance(),before)
if __name__=='__main__':unittest.main(verbosity=2)
