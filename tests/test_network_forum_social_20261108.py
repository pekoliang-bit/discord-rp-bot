"""Offline network forum/social regression: isolation, ID validation and idempotency."""
from __future__ import annotations
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core import network_groups as ng
from core import network_social_posts as posts

ROOT=Path(__file__).resolve().parents[1]


class NetworkForumSocialTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patcher=patch.object(database,'DATABASE_PATH',Path(directory.name)/'social.sqlite')
        patcher.start()
        self.addCleanup(patcher.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES ('1','G1'),('2','G2')")
            db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES "
                       "(10,'1','100','甲','active'),(11,'1','101','乙','active'),"
                       "(12,'2','102','丙','active')")
        self.a=ng.create_account('1','100',10,'Alpha')
        self.b=ng.create_account('1','101',11,'Bravo')
        self.c=ng.create_account('2','102',12,'Charlie')

    def test_bindings_are_guild_scoped(self):
        posts.bind_forum('1',123456)
        self.assertEqual(posts.bound_forum('1'),123456)
        self.assertIsNone(posts.bound_forum('2'))

    def test_identity_requires_both_handle_and_character_id(self):
        self.assertEqual(posts.resolve_identity('1','Bravo',11)['id'],self.b)
        self.assertIsNone(posts.resolve_identity('1','Bravo',10))
        self.assertIsNone(posts.resolve_identity('2','Bravo',11))

    def test_follow_owner_and_idempotency(self):
        posts.follow('1','100',self.a,self.b)
        posts.follow('1','100',self.a,self.b)
        with database.get_connection() as db:
            count=db.execute('SELECT COUNT(*) FROM rp_network_follows').fetchone()[0]
        self.assertEqual(count,1)
        with self.assertRaises(PermissionError):
            posts.follow('1','101',self.a,self.b)
        with self.assertRaises(ValueError):
            posts.follow('1','100',self.a,self.a)
        with self.assertRaises(LookupError):
            posts.follow('1','100',self.a,self.c)

    def test_friend_request_accept_reject(self):
        posts.request_friend('1','100',self.a,self.b)
        self.assertEqual(len(posts.pending_friends('1','101',self.b)),1)
        with self.assertRaises(PermissionError):
            posts.decide_friend('1','100',self.b,self.a,True)
        posts.decide_friend('1','101',self.b,self.a,True)
        self.assertEqual(posts.pending_friends('1','101',self.b),[])
        with self.assertRaises(ValueError):
            posts.decide_friend('1','101',self.b,self.a,False)

    def test_post_and_reaction_ledger(self):
        posts.save_post('1','100',self.a,'90001',[45,46])
        self.assertEqual(posts.thread_post('1','90001')['character_id'],10)
        self.assertIsNone(posts.thread_post('2','90001'))
        self.assertTrue(posts.record_interaction('1','90001','800','101','comment'))
        self.assertFalse(posts.record_interaction('1','90001','800','101','comment'))
        self.assertTrue(posts.record_interaction('1','90001','90001','101','reaction','💗'))
        self.assertTrue(posts.record_interaction('1','90001','90001','101','reaction','🔥'))
        self.assertFalse(posts.record_interaction('1','90001','90001','101','reaction','🔥'))
        with self.assertRaises(PermissionError):
            posts.save_post('1','101',self.a,'90002',[])

    def test_comment_command_parser(self):
        for text in ['@Alpha #10 ✅','@多 字 帳號 ＃ 12 ➕']:
            self.assertIsNotNone(posts.ACTION_PATTERN.fullmatch(text))
        self.assertIsNone(posts.ACTION_PATTERN.fullmatch('Alpha #10 ✅'))

    def test_ui_is_grouped_and_legacy_view_preserved(self):
        home=(ROOT/'modules/network_group_ui.py').read_text(encoding='utf-8-sig')
        live=(ROOT/'modules/live_stream_ui.py').read_text(encoding='utf-8-sig')
        forum=(ROOT/'modules/network_forum_ui.py').read_text(encoding='utf-8-sig')
        for src in (home,live,forum): ast.parse(src)
        for name in ['class NetworkHome','class AccountPicker','class GroupsView']:
            self.assertIn(name,home)
        self.assertIn('class LiveOrganizedView',live)
        self.assertIn('class WatchLiveView',live)
        self.assertIn('class ForumPostView',forum)
        self.assertIn('available_tags',forum)

if __name__=='__main__':unittest.main()
