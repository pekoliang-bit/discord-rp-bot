"""Release safety tests: no unapproved or incompatible awards may be paid."""
import unittest
from modules.leaderboard_payout_policy import inspect_reward, PAYOUT_ENABLED

class TestPolicy(unittest.TestCase):
    def reward(self, **kw):
        result = dict(board='heat', rank=1, coins=100, values={'heat': 10}, requires_adapter=True, reward_version='v11-approved')
        result.update(kw)
        return result

    def test_release_disabled(self):
        self.assertFalse(PAYOUT_ENABLED)
        self.assertIn('尚未啟用', '；'.join(inspect_reward('heat','lbentity:character:1:1',self.reward())['issues']))

    def test_no_provisional(self):
        self.assertFalse(inspect_reward('heat','lbentity:character:1:1',self.reward(reward_version='v11-provisional'))['allowed'])

    def test_no_unknown_version(self):
        self.assertFalse(inspect_reward('heat','lbentity:character:1:1',self.reward(reward_version='not-reviewed'))['allowed'])

    def test_no_wrong_board(self):
        self.assertFalse(inspect_reward('acting','lbentity:character:1:1',self.reward())['allowed'])

    def test_no_negative_fans(self):
        self.assertFalse(inspect_reward('antis','lbentity:character:1:1',self.reward(values={'black_fans':20}))['allowed'])

    def test_no_non_character_adapter(self):
        result = self.reward(board='industry', values={'business_heat': 10})
        self.assertFalse(inspect_reward('industry','lbentity:company:1:2',result)['allowed'])
        self.assertIn('非角色型', '；'.join(inspect_reward('industry','lbentity:company:1:2',result)['issues']))

    def test_no_unmapped_fans(self):
        self.assertIn('適配器','；'.join(inspect_reward('fans','lbentity:character:1:1',self.reward(board='fans',values={'fans':5}))['issues']))

if __name__ == '__main__': unittest.main()
