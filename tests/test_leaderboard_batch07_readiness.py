import unittest
from modules.leaderboard_payout_readiness import inspect
from modules.leaderboard_entity_identity import make_identity
class Readiness(unittest.TestCase):
 def test_non_character_is_locked(self):
  report=inspect('industry',make_identity('company',12),{'board':'industry','rank':1,'reward_version':'v11-approved','requires_adapter':True,'coins':3})
  self.assertFalse(report['allowed']);self.assertTrue(report['recipient']['requires_verified_adapter'])
 def test_character_stays_locked(self):
  report=inspect('wealth',make_identity('character',12),{'board':'wealth','rank':1,'reward_version':'v11-approved','requires_adapter':True})
  self.assertFalse(report['allowed'])
 def test_bare_id_rejected(self):
  self.assertFalse(inspect('wealth','12',{})['allowed'])
if __name__=='__main__':unittest.main()
