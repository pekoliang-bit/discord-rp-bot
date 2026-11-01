"""第24階段回歸測試：模擬報告 + 唯一稽核快照。"""
from __future__ import annotations
import os
import tempfile
import unittest

from modules.faction_action_simulation_report import generate_report


class Phase24SimulationTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(ability_weight=0.1, funding_weight=2.0,
            defense_reduction=4.0, full_block_probability=10.0,
            discovery_probability=20.0)

    def test_report_is_blocked_and_cost_matches_level(self):
        r=generate_report(guild_id='g',proposal_id=101,policy_revision=2,
            config=self.config,attacker_ability=50,target_resistance=40,
            level=3,block_roll=75,sabotage_roll=45,discovery_roll=30)
        self.assertEqual(r['quoted_cost'],100000)
        self.assertFalse(r['execution_enabled'])
        self.assertTrue(r['is_simulation'])

    def test_missing_config_fails_closed(self):
        with self.assertRaises(ValueError):
            generate_report(guild_id='g',proposal_id=101,policy_revision=2,
                config={},attacker_ability=50,target_resistance=40,
                level=1,block_roll=75,sabotage_roll=45,discovery_roll=30)

    def test_invalid_revision_fails(self):
        with self.assertRaises(ValueError):
            generate_report(guild_id='g',proposal_id=101,policy_revision=0,
                config=self.config,attacker_ability=50,target_resistance=40,
                level=1,block_roll=75,sabotage_roll=45,discovery_roll=30)


if __name__ == '__main__':
    unittest.main()
