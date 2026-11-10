"""頻道行動申請核准、實際顯示與重複保護，使用隔離 SQLite。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import database
from core.channel_action_registry import approve, list_channel_actions
from core.action_application_rules import parse_rewards, preview_cost


class ActionApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(self.temp.name)/'test.db')
        p.start();self.addCleanup(p.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES('444')")
        self.channel=SimpleNamespace(id=555,name='測試餐廳',guild=SimpleNamespace(id=444))
        self.guild=SimpleNamespace(get_channel=lambda cid: self.channel if cid==555 else None)

    def _case(self):
        return {'id':12,'guild_id':'444','kind':'action_expand','details':json.dumps({
            'schema':'action_application_v1','pricing':'actual_roll_b',
            'metadata':{'channel_id':'555','channel_name':'測試餐廳'},'actions':[
            {'name':'季節料理課','category':'課程','base_coins':3000,
             'stat_rewards':parse_rewards('容貌:4\n才智:隨機'), 'notes':''},
            {'name':'假日兼職','category':'打工','base_coins':500,
             'stat_rewards':[], 'notes':''},
        ]},ensure_ascii=False)}

    def test_approved_actions_visible_with_expected_cost(self):
        with database.transaction() as db:
            self.assertEqual(approve(db,self._case(),self.guild),2)
        actions,name=list_channel_actions('444','555')
        self.assertEqual(name,'測試餐廳')
        self.assertEqual(len(actions),2)
        course=next(a for a in actions if a['category']=='課程')
        self.assertEqual(preview_cost('課程',course['cost_coins'],course['stat_rewards']),
                         {'min':3000,'max':9500,'variable':True})

    def test_second_approval_rolls_back(self):
        with database.transaction() as db:
            approve(db,self._case(),self.guild)
        with self.assertRaises((ValueError,Exception)):
            with database.transaction() as db:
                approve(db,self._case(),self.guild)
        self.assertEqual(len(list_channel_actions('444','555')[0]),2)

    def test_missing_channel_blocked(self):
        bad=SimpleNamespace(get_channel=lambda cid:None)
        with self.assertRaises(ValueError):
            with database.transaction() as db:
                approve(db,self._case(),bad)
        self.assertEqual(list_channel_actions('444','555')[0],[])


if __name__=='__main__': unittest.main()
