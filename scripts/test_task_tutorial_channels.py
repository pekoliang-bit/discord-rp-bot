"""隔離資料庫驗證新手角色、帖子、事件去重及畢業限制。
執行 python -m unittest scripts.test_task_tutorial_channels -v
"""
import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import task_newbie, task_repository


class TutorialChannelTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.dbpath = str(Path(self.folder.name) / 'test.sqlite3')

        self.connections = []

        def connection():
            c = sqlite3.connect(self.dbpath)
            c.row_factory = sqlite3.Row
            self.connections.append(c)
            return c

        @contextlib.contextmanager
        def transaction():
            with connection() as c:
                c.execute('BEGIN IMMEDIATE')
                yield c
                c.commit()

        self.patchers = [
            patch.object(task_newbie, 'get_connection', connection),
            patch.object(task_repository, 'get_connection', connection),
            patch.object(task_repository, 'transaction', transaction),
        ]
        for p in self.patchers:
            p.start()
        with connection() as c:
            c.execute('CREATE TABLE characters (id INTEGER, guild_id TEXT, owner_id TEXT, status TEXT, character_type TEXT)')
            c.execute('CREATE TABLE character_forum_links (guild_id TEXT, character_id INTEGER, bot_id TEXT)')
            c.execute("INSERT INTO characters VALUES (1,'10','20','active','player')")
            c.execute("INSERT INTO characters VALUES (2,'10','21','active','player')")
            c.execute("INSERT INTO character_forum_links VALUES ('10',1,'100')")
            c.execute("INSERT INTO character_forum_links VALUES ('10',2,'200')")
        task_repository.initialize()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        for connection in self.connections:
            connection.close()
        self.folder.cleanup()

    def test_own_thread_only(self):
        self.assertTrue(task_newbie.can_use_training_channel(10,1,20,100,'查看'))
        self.assertFalse(task_newbie.can_use_training_channel(10,1,20,200,'查看'))
        self.assertFalse(task_newbie.can_use_training_channel(10,1,21,100,'查看'))

    def test_repeat_does_not_increase_progress(self):
        self.assertTrue(task_newbie.record_success(10,1,20,100,'查看'))
        self.assertFalse(task_newbie.record_success(10,1,20,100,'查看'))
        self.assertEqual(task_newbie.progress(10,1)['done'],1)
        self.assertEqual(task_newbie.progress(10,2)['done'],0)

    def test_graduation_revokes_training_exception(self):
        for cmd in task_newbie.REQUIRED_COMMANDS:
            self.assertTrue(task_newbie.record_success(10,1,20,100,cmd))
        self.assertTrue(task_newbie.is_graduated(10,1))
        self.assertFalse(task_newbie.can_use_training_channel(10,1,20,100,'劇情'))

    def test_names_not_used_as_authority(self):
        self.assertFalse(task_newbie.record_success(10,1,21,100,'查看'))
        self.assertFalse(task_newbie.record_success(10,1,20,999,'查看'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
