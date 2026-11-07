"""／行動專業點數：可用、權限、原子性及唯一交易測試。"""
from __future__ import annotations

import tempfile
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core.action_execution import initialize
from modules.action_professional_points import allocate, get_balance
from modules.leaderboard_points_confirmation import create_request, confirm_request


class ProfessionalPointsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        guard = patch.object(database, 'DATABASE_PATH', Path(self.temp.name) / 'db.sqlite3')
        guard.start()
        self.addCleanup(guard.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id) VALUES('guild')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('guild','owner','角色','active')")
            self.cid = db.execute("SELECT id FROM characters").fetchone()['id']
            initialize(db)
            db.execute('INSERT INTO rp_action_professional_points VALUES(?,?,?)', ('guild',self.cid,8))

    def test_action_ui_and_points_ui_are_syntactically_loadable(self):
        root = Path(__file__).resolve().parents[1]
        for file in ('modules/location_action_commands.py', 'modules/leaderboard_points_discord.py'):
            with self.subTest(file=file):
                ast.parse((root / file).read_text(encoding='utf-8'))
        self.assertIn('label="專業點數"',
            (root / 'modules/leaderboard_points_discord.py').read_text(encoding='utf-8'))

    def test_spend_skill(self):
        result=allocate('guild',self.cid,'owner','歌唱',3,'one')
        self.assertEqual(result['balance'],5)
        with database.get_connection() as db:
            self.assertEqual(db.execute("SELECT skill_value FROM character_skills WHERE skill_name='歌唱'").fetchone()[0],3)

    def test_retry_no_double_spend(self):
        allocate('guild',self.cid,'owner','歌唱',3,'two')
        self.assertEqual(allocate('guild',self.cid,'owner','歌唱',3,'two')['status'],'already_processed')
        self.assertEqual(get_balance('guild',self.cid),5)

    def test_collision_rejected(self):
        allocate('guild',self.cid,'owner','歌唱',3,'same')
        with self.assertRaisesRegex(ValueError,'相同操作'):
            allocate('guild',self.cid,'owner','歌唱',4,'same')

    def test_cannot_use_others_character(self):
        with self.assertRaises(PermissionError):
            allocate('guild',self.cid,'intruder','歌唱',1,'bad')
        self.assertEqual(get_balance('guild',self.cid),8)

    def test_insufficient_balance_rollback(self):
        with self.assertRaisesRegex(ValueError,'不足'):
            allocate('guild',self.cid,'owner','歌唱',10,'over')
        self.assertEqual(get_balance('guild',self.cid),8)
        with database.get_connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='rp_action_professional_history'").fetchone())

    def test_rejects_attribute_and_restricted(self):
        for target in ('影響指數','容貌','不存在技能'):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    allocate('guild',self.cid,'owner',target,1,'bad-'+target)
        self.assertEqual(get_balance('guild',self.cid),8)

    def test_preview_confirmation(self):
        request=create_request('guild',self.cid,'owner','professional','skill','歌唱',2)
        with self.assertRaises(PermissionError):
            confirm_request(request['token'],'other')
        self.assertEqual(confirm_request(request['token'],'owner')['balance'],6)
        with self.assertRaises(ValueError):
            confirm_request(request['token'],'owner')

    def test_unauthorized_preview_rejected(self):
        with self.assertRaises(PermissionError):
            create_request('guild',self.cid,'other','professional','skill','歌唱',1)

    def test_negative_and_float_denied(self):
        for bad in (0,-1,1.5,True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    allocate('guild',self.cid,'owner','歌唱',bad,'invalid')

    def test_guild_separation(self):
        with self.assertRaises(ValueError):
            allocate('other',self.cid,'owner','歌唱',1,'other')
        self.assertEqual(get_balance('other',self.cid),0)


if __name__ == '__main__':
    unittest.main()
