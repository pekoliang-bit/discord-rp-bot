
"""任務資料庫端到端：隔離資料庫，不連接 Discord，也不修改正式角色。"""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import core.database as database
from modules.task_repository import initialize,publish_task,record_event
from modules.task_newbie import REQUIRED_COMMANDS,progress
from modules.task_conditions import set_profile
from modules.task_service import character_info,eligible,accept,report
from modules.task_review import decide
from modules.task_reward_worker import retry_pending


class TaskDatabaseIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.previous=database.DATABASE_PATH
        database.DATABASE_PATH=Path(self.tmp.name)/'task_test.sqlite3'
        database.init_db()
        database.register_guild('123','test')
        self.cid=database.create_character('123','789','測試人物',age=24,occupation='演員')
        with database.transaction() as db:
            db.execute('UPDATE characters SET status=? WHERE guild_id=? AND id=?',('active','123',self.cid))
            db.execute('CREATE TABLE rp_character_status (guild_id TEXT, character_id INTEGER, level INTEGER, PRIMARY KEY(guild_id,character_id))')
            db.execute('INSERT INTO rp_character_status VALUES (?,?,?)',('123',self.cid,5))
            db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',('123',self.cid,'演繹',100))
        initialize()
        for cmd in REQUIRED_COMMANDS:
            record_event('123',self.cid,'newbie:'+cmd,'command:'+cmd)
        self.assertTrue(progress('123',self.cid)['graduated'])

    def tearDown(self):
        database.DATABASE_PATH=self.previous
        self.tmp.cleanup()

    def test_cafe_attribute_and_report_to_claim(self):
        set_profile('123','電影',3,{'min_cafe_level':5,'min_attributes':{'演繹':90}})
        publish_task('123','work_1','screen','正式影片通告',subtype='電影',level=3,
            requirements={},rewards={},report={'mode':'manual','channel_id':'999'},status='published')
        char=character_info('123',self.cid,'789')
        self.assertEqual(char['cafe_level'],5)
        with database.get_connection() as db:
            task=dict(db.execute("SELECT * FROM rp_task_definitions WHERE code='work_1'").fetchone())
        self.assertTrue(eligible('123',task,char)['eligible'])
        self.assertTrue(accept('123',self.cid,'789',task['id']))
        self.assertFalse(accept('123',self.cid,'789',task['id']))
        proof={'message_id':'888888','channel_id':'999','author_id':'789',
               'created_at':datetime.now(timezone.utc),
               'content':'這是演出通告的正式回報文章，已經達到最低內容長度。'}
        self.assertTrue(report('123',self.cid,'789',task['id'],'999',evidence=proof))
        with database.get_connection() as db:
            row=db.execute('SELECT * FROM rp_task_evidence').fetchone()
            self.assertEqual(row['message_id'],'888888')
        result=decide('123','777','777',self.cid,task['id'],'once',True)
        self.assertTrue(result.get('claimed'),result)
        self.assertEqual(retry_pending('123')['attempted'],0)
        with self.assertRaises(ValueError):
            decide('123','777','777',self.cid,task['id'],'once',True)

    def test_missing_evidence_cannot_submit(self):
        publish_task('123','work_2','limited','限時任務',requirements={},rewards={},
            report={'mode':'manual','channel_id':'999'},status='published')
        with database.get_connection() as db:
            task_id=db.execute("SELECT id FROM rp_task_definitions WHERE code='work_2'").fetchone()['id']
        accept('123',self.cid,'789',task_id)
        with self.assertRaises(ValueError):report('123',self.cid,'789',task_id,'999')
        with database.get_connection() as db:
            row=db.execute('SELECT status FROM rp_task_progress').fetchone()
        self.assertEqual(row['status'],'accepted')

if __name__=='__main__':unittest.main(verbosity=2)
