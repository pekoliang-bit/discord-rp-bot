"""離線純函式測試；可用 python -m unittest tests/test_faction_phase26.py 執行。"""
from __future__ import annotations
import unittest
from modules.faction_action_rule_validation import validate_action_policy
from modules.faction_action_notice_payload import make_action_notice

class Phase26Tests(unittest.TestCase):
    def setUp(self):
        self.policy=dict(ability_weight=1,funding_weight=1,defense_reduction=5,
                         full_block_probability=20,discovery_probability=10,
                         effect_duration_seconds=3600,effect_strength=3)
    def test_policy_valid(self):
        self.assertEqual(validate_action_policy(self.policy)['effect_duration_seconds'],3600)
    def test_policy_missing_or_invalid(self):
        data=dict(self.policy);del data['ability_weight']
        with self.assertRaises(ValueError):validate_action_policy(data)
        data=dict(self.policy);data['discovery_probability']=101
        with self.assertRaises(ValueError):validate_action_policy(data)
    def test_anon_notice(self):
        n=make_action_notice(proposal_id=7,action_type='sabotage',outcome='failed',
                             target_character_id=9,source_faction_name='秘密陣營')
        self.assertIsNone(n['source_faction'])
        self.assertFalse(n['sent'])
    def test_disclosed_notice(self):
        n=make_action_notice(proposal_id=8,action_type='defense',outcome='blocked',
                             target_character_id=9,source_faction_name='A',source_disclosed=True)
        self.assertEqual(n['source_faction'],'A')

if __name__=='__main__':unittest.main()
