import unittest
from modules.leaderboard_entity_identity import make_identity
from modules.leaderboard_recipient_resolution import resolve
class RecipientTest(unittest.TestCase):
    def test_routes_are_distinct(self):
        routes={resolve(board,make_identity(t,'1'))['route'] for board,t in [('heat','character'),('industry','company'),('groups','group'),('shops','shop'),('factions','faction'),('cp','cp'),('social','social_account')]}
        self.assertEqual(len(routes),7)
    def test_only_character_can_skip_adapter_requirement(self):
        self.assertFalse(resolve('heat',make_identity('character','123'))['requires_verified_adapter'])
        self.assertTrue(resolve('industry',make_identity('company','123'))['requires_verified_adapter'])
if __name__=='__main__':unittest.main()
