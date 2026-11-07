"""162 個已核准效果、已持有標籤、舊名稱遷移、相對加成及同類效果取最高。"""
from __future__ import annotations
import csv
import tempfile
import unittest
from pathlib import Path

import core.database as database
from modules import tag_library as tags
from core.tag_effects import evaluate_tag_effects


class ApprovedTagEffects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.tmp.name) / 'tag.sqlite3'
        self.addCleanup(setattr, database, 'DATABASE_PATH', old)
        tags._SEEDED_GUILDS.discard('714')
        with database.transaction() as db:
            db.execute('CREATE TABLE guilds(guild_id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE characters(id INTEGER PRIMARY KEY,guild_id TEXT,owner_id TEXT,character_type TEXT)')
            db.execute("INSERT INTO guilds VALUES ('714')")
            db.execute("INSERT INTO characters VALUES (11,'714','23','player')")

    def test_full_catalog_approved_with_exact_names(self):
        rows=tags.catalog()
        self.assertEqual(len(rows),162)
        self.assertEqual({r['效果審核狀態'] for r in rows},{'已核准'})
        by_code={r['編號']:r for r in rows}
        self.assertEqual(by_code['019']['標籤名稱'],'表情管理教科書')
        self.assertEqual(by_code['041']['標籤名稱'],'寵粉擔當')
        self.assertEqual(by_code['106']['標籤名稱'],'塌房絕緣體')
        self.assertEqual(by_code['028']['標籤名稱'],'0國語言擁有者')
        self.assertEqual(len({r['標籤名稱'] for r in rows}),162)
        self.assertNotIn('名稱待確認', ''.join(r['加成／風險候選'] for r in rows))

    def test_held_and_verified_only(self):
        tags.replace_initial('714',11,'23','23',['行走的CD','舞台精靈'])
        rule=tags.numeric_rules('行走的CD')[0]
        self.assertEqual(rule['relative_percent'],10)
        result=evaluate_tag_effects('714',11,0.2,stage='現場演唱',verified_effects=[('046',0)],cap=(0,1))
        self.assertAlmostEqual(result.adjusted,.22)
        self.assertEqual(result.sources[0][0],'046')
        self.assertEqual(evaluate_tag_effects('714',11,.2,stage='其他',verified_effects=[]).adjusted,.2)
        self.assertEqual(evaluate_tag_effects('714',11,.2,stage='其他',verified_effects=[('099',0)]).adjusted,.2)

    def test_choose_strongest_in_same_stage(self):
        tags.replace_initial('714',11,'23','23',['行走的CD','舞台精靈'])
        result=evaluate_tag_effects('714',11,.2,stage='舞台',verified_effects=[('046',0),('040',0)])
        self.assertEqual(result.positive_percent,10)
        self.assertAlmostEqual(result.adjusted,.22)

    def test_negative_and_positive_are_relative(self):
        tags.replace_initial('714',11,'23','23',['舞台黑洞','遊戲黑洞'])
        result=evaluate_tag_effects('714',11,.2,stage='綜藝遊戲',verified_effects=[('043',0),('043',1),('047',0),('047',1)])
        self.assertEqual(result.positive_percent,10)
        self.assertEqual(result.negative_percent,-10)
        self.assertAlmostEqual(result.adjusted,.198)
        self.assertEqual(len(result.sources),2)

    def test_pure_image_tags_not_numeric(self):
        self.assertEqual(tags.numeric_rules('萌系'),[])
        tags.replace_initial('714',11,'23','23',['萌系'])
        self.assertEqual(evaluate_tag_effects('714',11,10,stage='演出',verified_effects=[('006',0)]).adjusted,10)

    def test_legacy_names_preserve_held_and_history(self):
        tags.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO rp_tag_library(guild_id,name,category) VALUES ('714','表管教科書','大眾印象')")
            old_id=db.execute("SELECT id FROM rp_tag_library WHERE name='表管教科書'").fetchone()['id']
            db.execute("INSERT INTO rp_character_tags(guild_id,character_id,tag_id) VALUES ('714',11,?)",(old_id,))
            db.execute("INSERT INTO rp_tag_history(guild_id,character_id,tag_id,tag_name,action,source) VALUES ('714',11,?,'表管教科書','add','initial')",(old_id,))
        tags.seed('714')
        self.assertEqual([t['name'] for t in tags.held('714',11)],['表情管理教科書'])
        self.assertEqual([t['tag_name'] for t in tags.history('714',11)],['表情管理教科書'])
        self.assertEqual([t['name'] for t in tags.tags('714') if t['name']=='表管教科書'],[])
        self.assertEqual(tags.held('714',11)[0]['id'],old_id)

    def test_legacy_conflict_merges_without_losing_ownership(self):
        tags.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO rp_tag_library(guild_id,name,category) VALUES ('714','魅粉擔當','大眾印象')")
            db.execute("INSERT INTO rp_tag_library(guild_id,name,category) VALUES ('714','寵粉擔當','大眾印象')")
            old_id=db.execute("SELECT id FROM rp_tag_library WHERE name='魅粉擔當'").fetchone()['id']
            new_id=db.execute("SELECT id FROM rp_tag_library WHERE name='寵粉擔當'").fetchone()['id']
            db.execute("INSERT INTO rp_character_tags(guild_id,character_id,tag_id) VALUES ('714',11,?)",(old_id,))
        tags.seed('714')
        self.assertEqual([t['name'] for t in tags.held('714',11)], ['寵粉擔當'])
        self.assertEqual(tags.held('714',11)[0]['id'],new_id)

    def test_no_permanent_attributes_and_no_unverified_condition(self):
        tags.replace_initial('714',11,'23','23',['塌房絕緣體'])
        self.assertEqual(evaluate_tag_effects('714',11,.5,stage='品牌',verified_effects=[]).adjusted,.5)
        self.assertAlmostEqual(evaluate_tag_effects('714',11,.5,stage='品牌',verified_effects=[('106',0)]).adjusted,.525)
        with database.get_connection() as db:
            self.assertEqual({r['name'] for r in db.execute('SELECT name FROM sqlite_master WHERE type="table"') if 'attribute' in r['name']},set())

if __name__=='__main__': unittest.main()
