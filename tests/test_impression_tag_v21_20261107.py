"""V2.1 catalogue and initial-tag constraints; offline using isolated SQLite."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules import tag_library as tags
import core.database as database


class TagV21Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dbfile = Path(self.temp.name) / 'test.sqlite3'
        original = database.DATABASE_PATH
        database.DATABASE_PATH = self.dbfile
        self.addCleanup(setattr, database, 'DATABASE_PATH', original)
        tags._SEEDED_GUILDS.discard('731111')
        with database.transaction() as db:
            db.execute('CREATE TABLE guilds (guild_id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE characters (id INTEGER PRIMARY KEY, guild_id TEXT, owner_id TEXT, character_type TEXT)')
            db.execute('INSERT INTO guilds VALUES (?)', ('731111',))
            db.execute("INSERT INTO characters VALUES (5,'731111','101','player')")
            db.execute("INSERT INTO characters VALUES (6,'731111','102','player_npc')")

    def test_catalog_162_and_unique(self):
        rows = tags.catalog()
        self.assertEqual(len(rows), 162)
        self.assertEqual(len({row['æ¨ç±¤åç¨±'] for row in rows}), 162)
        self.assertEqual({row['ææå¯©æ ¸çæ'] for row in rows}, {'å·²æ ¸å'})

    def test_seed_and_old_defaults(self):
        names = {row['name'] for row in tags.tags('731111')}
        self.assertTrue({row['æ¨ç±¤åç¨±'] for row in tags.catalog()}.issubset(names))
        self.assertNotIn('æº«æç¾é', names)

    def test_initial_player_limit_two(self):
        names = [r['æ¨ç±¤åç¨±'] for r in tags.catalog()]
        self.assertEqual(tags.replace_initial('731111', 5, '101', '101', names[:2]), names[:2])
        with self.assertRaises(ValueError):
            tags.replace_initial('731111', 5, '101', '101', names[:3])
        self.assertEqual(len(tags.held('731111', 5)), 2)

    def test_npc_three(self):
        names = [r['æ¨ç±¤åç¨±'] for r in tags.catalog()]
        tags.seed('731111')
        self.assertEqual(len(tags.replace_initial('731111',6,'102','102',names[:3])),3)

    def test_owner_isolation(self):
        tags.seed('731111')
        with self.assertRaises(PermissionError):
            tags.replace_initial('731111',5,'102','102',[tags.catalog()[0]['æ¨ç±¤åç¨±']])

    def test_more_than_five_acquired_via_rumor(self):
        names = [r['æ¨ç±¤åç¨±'] for r in tags.catalog()]
        tags.seed('731111')
        tags.replace_initial('731111',5,'101','101',names[:2])
        tags.rumor_change('731111',5,add=names[2:9],source_ref='case-1')
        self.assertEqual(len(tags.held('731111',5)),9)
        self.assertEqual(len(tags.representative('731111',5)),5)
        self.assertEqual(len(tags.history('731111',5)),9)

    def test_approved_effect_does_not_credit_attributes(self):
        item=tags.catalog()[0]
        public=tags.effect_metadata('731111',item['æ¨ç±¤åç¨±'])
        private=tags.effect_metadata('731111',item['æ¨ç±¤åç¨±'],privileged=True)
        self.assertIsNone(public['effect'])
        self.assertEqual(private['approval'],'å·²æ ¸å')
        self.assertTrue(private['numeric_effect_active'])

    def test_rule_percentages_use_exact_source_values(self):
        rules=tags.numeric_rules('èå°é»æ´')
        self.assertEqual([rule['relative_percent'] for rule in rules],[10,-5])
        self.assertEqual(tags.apply_relative(0.20,10), 0.22000000000000003)
        self.assertEqual(tags.numeric_rules('èç³»'),[])

    def test_all_python_sources_parse(self):
        import ast
        root=Path(__file__).resolve().parents[1]
        for path in root.rglob('*.py'):
            with self.subTest(path=path):
                ast.parse(path.read_text(encoding='utf-8-sig'))

if __name__=='__main__':
    unittest.main()
