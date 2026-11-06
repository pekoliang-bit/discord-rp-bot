import sqlite3,unittest
from modules.leaderboard_account_adapter import inspect_account
from modules.leaderboard_entity_identity import make_identity
from tests.test_leaderboard_batch07_accounts import fixture
class NoMutation(unittest.TestCase):
 def test_read_only_query(self):
  db=fixture();before=db.total_changes
  p=inspect_account(db,'1','industry',make_identity('company',12))
  self.assertTrue(p['can_credit']);self.assertEqual(db.total_changes,before)
  db.close()
 def test_no_auto_create(self):
  db=fixture();db.execute("DELETE FROM accounts WHERE owner_type='organization'")
  before=db.total_changes
  p=inspect_account(db,'1','industry',make_identity('company',12))
  self.assertFalse(p['can_credit']);self.assertEqual(db.total_changes,before)
  db.close()
if __name__=='__main__':unittest.main()
