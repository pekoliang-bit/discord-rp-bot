import unittest
import json
from modules.leaderboard_entity_identity import make_identity
from modules.leaderboard_payout_readiness import inspect
from modules.leaderboard_payout_preview import preview_award
class ReadinessTest(unittest.TestCase):
    def test_never_auto_enable(self):
        reward={'board':'industry','rank':1,'coins':100,'values':{},'reward_version':'v11-approved','requires_adapter':True}
        result=inspect('industry',make_identity('company','3'),reward)
        self.assertFalse(result['allowed'])
        self.assertIn('company_treasury',repr(result['recipient']))
    def test_provisional_blocked(self):
        reward={'board':'heat','rank':1,'coins':100,'values':{},'reward_version':'v11-provisional','requires_adapter':True}
        result=inspect('heat',make_identity('character','3'),reward)
        self.assertFalse(result['allowed'])
    def test_14d_not_allowed(self):
        reward={'board':'heat','rank':1,'coins':100,'values':{},'reward_version':'v11-approved','requires_adapter':True}
        out=preview_award({'guild_id':'1','cycle':'14d','idx':1,'board':'heat','entity_key':make_identity('character','3'),'rank':1,'reward_json':json.dumps(reward),'state':'approved'})
        self.assertFalse(out['can_pay'])
        self.assertTrue(any('14å¤©' in issue for issue in out['issues']))
if __name__=='__main__':unittest.main()
