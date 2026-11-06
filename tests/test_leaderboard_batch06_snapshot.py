import unittest
from unittest.mock import patch
from modules.leaderboard_entity_identity import make_identity
from modules.leaderboard_snapshot import recipient_key,normalize_board,collect,AmbiguousRecipient
class SnapshotTest(unittest.TestCase):
    def test_character_and_company_do_not_collide(self):
        self.assertNotEqual(recipient_key('heat',{'character_id':42}),recipient_key('industry',{'company_id':42}))
    def test_row_id_fallback(self):
        self.assertEqual(recipient_key('shops',{'id':9}),make_identity('shop','9'))
    def test_foreign_field_blocked(self):
        with self.assertRaises(AmbiguousRecipient):recipient_key('groups',{'character_id':8,'id':8})
    def test_duplicate_blocked(self):
        with self.assertRaises(AmbiguousRecipient):normalize_board('heat',[{'character_id':1,'rank':1,'score':8},{'character_id':1,'rank':1,'score':8}])
    def test_bad_typed_key_blocked(self):
        with self.assertRaises(AmbiguousRecipient):recipient_key('heat',{'entity_key':make_identity('company',1)})
    def test_unavailable_instead_of_fake_snapshot(self):
        with patch('modules.leaderboard_snapshot.leaderboard',return_value=([{'score':10,'rank':1}], 'ready')):
            records,unavailable=collect('guild')
        self.assertIn('heat',unavailable)
        self.assertNotIn('heat',records)
if __name__=='__main__':unittest.main()
