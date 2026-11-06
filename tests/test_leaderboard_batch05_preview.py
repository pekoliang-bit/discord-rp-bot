"""Preview must remain conservative even for malformed external rows."""
import unittest
from modules.leaderboard_payout_preview import preview_award

class PreviewTest(unittest.TestCase):
    def row(self, **changes):
        import json
        value=dict(guild_id='1', cycle='30d', idx=1, board='heat', entity_key='lbentity:character:1:1', rank=1, state='ready', reward_json=json.dumps(dict(board='heat',rank=1,coins=100,values={'heat':10},requires_adapter=True,reward_version='v11-approved')))
        value.update(changes)
        return value

    def test_previews_are_never_payable_while_disabled(self):
        self.assertFalse(preview_award(self.row())['can_pay'])

    def test_wrong_cycle(self):
        self.assertIn('14天', '；'.join(preview_award(self.row(cycle='14d'))['issues']))

    def test_wrong_rank(self):
        self.assertIn('名次', '；'.join(preview_award(self.row(rank=2))['issues']))

    def test_invalid_json(self):
        self.assertFalse(preview_award(self.row(reward_json='not-json'))['can_pay'])

    def test_not_ready(self):
        self.assertIn('審核', '；'.join(preview_award(self.row(state='pending_adapter'))['issues']))

if __name__ == '__main__': unittest.main()
