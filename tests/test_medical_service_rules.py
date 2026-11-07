"""醫院 RP 規則回歸：已移除婦產/親子、分級價差、拒絕未核准操作。"""
import json
import unittest
from copy import deepcopy
from modules.medical_service_rules import load_services, validate_catalog, available_services

class HospitalRulesTest(unittest.TestCase):
    def test_no_old_services(self):
        d=load_services(); joined=json.dumps(d['services'],ensure_ascii=False)
        for x in ('產檢','生產','終止妊娠','pregnancy_termination','childbirth'):
            self.assertNotIn(x,joined)
    def test_cosmetic_price_reward_monotonic(self):
        d=load_services(); entries=[x for x in d['services'] if x['group'] in ('小整形','大整形')]
        self.assertEqual(len(entries),4)
        self.assertEqual([x['price_coins'] for x in entries],[120000,180000,350000,550000])
        self.assertEqual([x['success_bonus_points_proposed'] for x in entries],[8,14,20,32])
        self.assertTrue(validate_catalog(d))
    def test_not_available_to_players(self):
        self.assertEqual(available_services(),[])
        self.assertEqual(len(available_services(owner_preview=True)),10)
    def test_guard_rejects_unauthorized_activation(self):
        d=load_services();d['services'][0]['approved']=True
        with self.assertRaises(ValueError):validate_catalog(d)
    def test_guard_rejects_obstetric_services(self):
        d=load_services();e=dict(d['services'][0],id='childbirth',name='生產');d['services'].append(e)
        with self.assertRaises(ValueError):validate_catalog(d)
    def test_guard_rejects_risk_bonus_reversal(self):
        d=load_services();d['services'][-1]['success_bonus_points_proposed']=1
        with self.assertRaises(ValueError):validate_catalog(d)
if __name__=='__main__':unittest.main()
