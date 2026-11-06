import unittest,json
from modules.leaderboard_payout_preview import preview_award
from modules.leaderboard_entity_identity import make_identity
class Preview(unittest.TestCase):
 def row(self,cycle='30d'):
  return {'guild_id':'1','cycle':cycle,'idx':1,'board':'wealth','entity_key':make_identity('character',12),'rank':1,'state':'ready',
   'reward_json':json.dumps({'board':'wealth','rank':1,'reward_version':'v11-approved','requires_adapter':True})}
 def test_never_pay_without_db(self):
  p=preview_award(self.row());self.assertFalse(p['can_pay']);self.assertIn('未核對實際收款帳戶',p['issues'])
 def test_no_14d_reward(self):
  self.assertIn('14天小結算不能派發獎勵',preview_award(self.row('14d'))['issues'])
 def test_wrong_rank(self):
  r=self.row();r['rank']=2;self.assertFalse(preview_award(r)['can_pay'])
if __name__=='__main__':unittest.main()
