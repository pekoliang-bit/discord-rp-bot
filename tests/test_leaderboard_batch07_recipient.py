import unittest
from modules.leaderboard_recipient_resolution import resolve
from modules.leaderboard_entity_identity import make_identity
class Recipient(unittest.TestCase):
 def test_types(self):
  for board,typ in [('wealth','character'),('industry','company'),('groups','group'),('shops','shop'),('factions','faction'),('cp','cp'),('social','social_account')]:
   with self.subTest(typ=typ):self.assertEqual(resolve(board,make_identity(typ,12))['entity_type'],typ)
 def test_mixed_types(self):
  with self.assertRaises(ValueError):resolve('groups',make_identity('company',12))
 def test_missing_guild_for_database(self):
  with self.assertRaises(ValueError):resolve('wealth',make_identity('character',12),db=object())
if __name__=='__main__':unittest.main()
