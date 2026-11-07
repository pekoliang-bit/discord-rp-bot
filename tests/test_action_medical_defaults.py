"""行動：設計資料完整性、醫院未核准保護。

不依賴 Discord，無需 DB。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / 'assets' / 'action_locations'

class ActionMedicalDefaultsTests(unittest.TestCase):
    def test_six_ordinary_areas_and_medical(self):
        names = ('nightlife','leisure','training','arts','film','business')
        total_locations = total_actions = 0
        for name in names:
            data = json.loads((BASE / f'{name}_default_pending.json').read_text(encoding='utf-8'))
            total_locations += len(data['locations'])
            total_actions += sum(len(loc.get('actions', [])) for loc in data['locations'])
        self.assertEqual(total_locations, 31)
        self.assertEqual(total_actions, 266)

    def test_hospital_is_not_executable(self):
        hospital = json.loads((BASE/'medical_default_pending.json').read_text(encoding='utf-8'))
        self.assertEqual(len(hospital['services']), 10)
        self.assertFalse(any(x['id'] in {'childbirth','prenatal_care','pregnancy_termination'} for x in hospital['services']))
        self.assertEqual(hospital['location']['channel_id'], '1567998859362304100')
        self.assertFalse(hospital['location']['action_enabled'])
        self.assertFalse(hospital['location']['service_enabled'])
        self.assertTrue(all(not x['approved'] and not x['enabled'] for x in hospital['services']))
        self.assertTrue(all(x['requires_character_consent'] and x['private_result'] for x in hospital['services']))

    def test_beauty_center_is_non_medical(self):
        leisure = json.loads((BASE/'leisure_default_pending.json').read_text(encoding='utf-8'))
        center = next(loc for loc in leisure['locations'] if loc['key'] == 'beauty_center')
        self.assertEqual(len(center['actions']), 6)
        self.assertEqual(next(a for a in center['actions'] if a['category']=='課程' and a['name']=='美甲設計課')['skill_name'],'美甲')
        self.assertTrue(center['excluded_services'])
        self.assertTrue(all(not x['approved'] for x in center['actions']))

if __name__ == '__main__':
    unittest.main()
