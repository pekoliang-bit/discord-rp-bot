"""第六部分：社群關注與好友的角色狀態、跨帳號防護。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core import network_groups as ng
from core import network_social_posts as posts


class SocialSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        mock_path = patch.object(database, 'DATABASE_PATH', Path(directory.name) / 'test.sqlite')
        mock_path.start()
        self.addCleanup(mock_path.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES ('g','測試')")
            db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES "
                       "(11,'g','u1','甲','active'),(12,'g','u2','乙','active')")
        self.a = ng.create_account('g','u1',11,'甲帳號')
        self.a2 = ng.create_account('g','u1',11,'甲備用帳號')
        self.b = ng.create_account('g','u2',12,'乙帳號')

    def test_same_character_alternate_accounts_cannot_self_follow_or_friend(self):
        with self.assertRaises(ValueError):
            posts.follow('g','u1',self.a,self.a2)
        with self.assertRaises(ValueError):
            posts.request_friend('g','u1',self.a,self.a2)

    def test_inactive_character_cannot_receive_new_follow_or_friend_request(self):
        with database.transaction() as db:
            db.execute("UPDATE characters SET status='retired' WHERE guild_id='g' AND id=12")
        with self.assertRaises(LookupError):
            posts.follow('g','u1',self.a,self.b)
        with self.assertRaises(LookupError):
            posts.request_friend('g','u1',self.a,self.b)
        with database.get_connection() as db:
            posts.initialize(db)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_network_follows').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_network_friends').fetchone()[0],0)

    def test_active_different_characters_are_allowed(self):
        posts.follow('g','u1',self.a,self.b)
        posts.request_friend('g','u1',self.a,self.b)
        self.assertEqual(len(posts.pending_friends('g','u2',self.b)),1)

if __name__ == '__main__':
    unittest.main()
