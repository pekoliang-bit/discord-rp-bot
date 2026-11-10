"""核准的隨機點數與 B 方案：抽中後按實際點數計價。"""
import unittest
from unittest.mock import patch
from tests.test_location_action_execution import TestLocationActionExecution as Base
from core.action_execution import execute_action
from modules.action_reward_rules import validate_action
from core.action_application_rules import parse_rewards, preview_cost, ProposalError


class RandomPricingTests(unittest.TestCase):
    setUp = Base.setUp
    balance = Base.balance

    def test_random_roll_uses_actual_points_and_idempotency(self):
        action = dict(name='購物課程', category='課程', approved=True,
            cost_coins=1000, reward_coins=0, action_points_cost=0,
            stat_rewards=[{'name': '容貌', 'mode': 'random', 'min': 1, 'max': 15}])
        self.assertEqual(validate_action(action), (True, 'OK'))
        before = self.balance()
        with patch('core.action_execution.secrets.randbelow', return_value=7):
            result = execute_action('g', 'u', self.cid, 'store', action, 'random1')
        self.assertEqual(result['rolled_stats'], [{'name': '容貌', 'amount': 8}])
        self.assertEqual(result['cost_coins'], 4000)
        self.assertEqual(self.balance(), before - 4000)
        again = execute_action('g', 'u', self.cid, 'store', action, 'random1')
        self.assertTrue(again['duplicated'])
        self.assertEqual(again['rolled_stats'], result['rolled_stats'])
        self.assertEqual(self.balance(), before - 4000)

    def test_preview_and_forbidden_stat(self):
        x = parse_rewards('容貌:5\n才智:隨機')
        self.assertEqual(preview_cost('活動', 2000, x),
                         {'min': 2000, 'max': 6000, 'variable': True})
        with self.assertRaises(ProposalError):
            parse_rewards('影響指數:隨機')

    def test_insufficient_coins_rolls_back_random_gain(self):
        action = dict(name='高價訓練',category='課程',approved=True,
            cost_coins=0,reward_coins=0,action_points_cost=0,
            stat_rewards=[{'name':'容貌','mode':'random','min':1,'max':15}])
        before=self.balance()
        with patch('core.action_execution.secrets.randbelow',return_value=14):
            # 15*500=7500; force insufficient wallet through underlying data if necessary
            from core import database
            with database.transaction() as db:
                row=db.execute("SELECT id FROM accounts WHERE guild_id='g' AND owner_type='character' "
                    "AND owner_id=? AND account_type='wallet'",(str(self.cid),)).fetchone()
                from core.economy import _payment
                sink=database.get_or_create_account(db,'g','system','drain','treasury')
                _payment(db,'g','drain:pricing',row['id'],sink,15000,'測試用扣款')
            old=self.balance()
            with self.assertRaises(Exception):
                execute_action('g','u',self.cid,'store',action,'insufficient-random')
            self.assertEqual(self.balance(),old)
            with database.get_connection() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_location_action_events'").fetchone():
                    self.assertIsNone(db.execute("SELECT 1 FROM rp_location_action_events WHERE request_key='insufficient-random'").fetchone())


if __name__ == '__main__': unittest.main()
