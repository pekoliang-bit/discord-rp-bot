import sqlite3,unittest
from modules.leaderboard_account_adapter import inspect_account
from modules.leaderboard_entity_identity import make_identity

def fixture():
 db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
 db.executescript("""CREATE TABLE characters(guild_id TEXT,id INTEGER,status TEXT);
 CREATE TABLE rp_business_entities(guild_id TEXT,business_id TEXT,kind TEXT,status TEXT);
 CREATE TABLE personal_shops(guild_id TEXT,id INTEGER,status TEXT);
 CREATE TABLE faction_groups(guild_id TEXT,id INTEGER);
 CREATE TABLE accounts(id INTEGER PRIMARY KEY,guild_id TEXT,owner_type TEXT,owner_id TEXT,account_type TEXT);
 INSERT INTO characters VALUES ('1',12,'active');
 INSERT INTO rp_business_entities VALUES ('1','12','company','active');
 INSERT INTO rp_business_entities VALUES ('1','22','group','active');
 INSERT INTO personal_shops VALUES ('1',12,'open');
 INSERT INTO faction_groups VALUES ('1',12);
 INSERT INTO accounts VALUES (1,'1','character','12','wallet');
 INSERT INTO accounts VALUES (2,'1','organization','12','treasury');
 INSERT INTO accounts VALUES (3,'1','organization','22','treasury');
 """)
 return db
class Accounts(unittest.TestCase):
 def test_character(self):
  with fixture() as db:self.assertEqual(inspect_account(db,'1','wealth',make_identity('character',12))['account_id'],1)
 def test_company_separate(self):
  with fixture() as db:self.assertEqual(inspect_account(db,'1','industry',make_identity('company',12))['account_id'],2)
 def test_group(self):
  with fixture() as db:self.assertEqual(inspect_account(db,'1','groups',make_identity('group',22))['account_id'],3)
 def test_wrong_guild(self):
  with fixture() as db:self.assertFalse(inspect_account(db,'2','industry',make_identity('company',12))['can_credit'])
 def test_wrong_kind(self):
  with fixture() as db:self.assertFalse(inspect_account(db,'1','groups',make_identity('group',12))['can_credit'])
 def test_shop_not_fabricated(self):
  with fixture() as db:self.assertFalse(inspect_account(db,'1','shops',make_identity('shop',12))['can_credit'])
 def test_faction_not_fabricated(self):
  with fixture() as db:self.assertFalse(inspect_account(db,'1','factions',make_identity('faction',12))['can_credit'])
 def test_nonexistent(self):
  with fixture() as db:self.assertFalse(inspect_account(db,'1','wealth',make_identity('character',99))['can_credit'])
if __name__=='__main__':unittest.main()
