import unittest
from modules.leaderboard_entity_identity import make_identity
from modules.leaderboard_payout_identity import validate_identity,expected_type
class IdentityTest(unittest.TestCase):
    def test_all_routes(self):
        for board,kind in [('heat','character'),('industry','company'),('groups','group'),('shops','shop'),('factions','faction'),('cp','cp'),('social','social_account')]:
            self.assertEqual(validate_identity(board,make_identity(kind,'123'))['entity_type'],kind)
    def test_cross_type(self):
        with self.assertRaises(ValueError):validate_identity('industry',make_identity('character','123'))
    def test_raw_id(self):
        with self.assertRaises(ValueError):validate_identity('heat','123')
    def test_awards(self):
        with self.assertRaises(ValueError):expected_type('awards')
if __name__=='__main__':unittest.main()
