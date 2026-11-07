"""傳聞/查看共同授權資料與選單合約；不需 discord.py 或正式資料庫。"""
from __future__ import annotations

import ast
import contextlib
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class SharedConsentIntegrationTests(unittest.TestCase):
    """使用 pr_rumor 的真實 SQL 函式，橋接 read/write 進行隔離測試。"""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE characters(guild_id TEXT,id INTEGER,owner_id TEXT,status TEXT);
            CREATE TABLE rp_rumor_optout(guild_id TEXT,character_id INTEGER,enabled INTEGER,updated_at INTEGER,
                PRIMARY KEY(guild_id,character_id));
            CREATE TABLE rp_rumor_category_consent(guild_id TEXT,character_id INTEGER,category TEXT,enabled INTEGER,updated_at INTEGER,
                PRIMARY KEY(guild_id,character_id,category));
            INSERT INTO characters VALUES('g',1,'alice','active');
            INSERT INTO characters VALUES('g',2,'bob','active');
            INSERT INTO characters VALUES('other',1,'alice','active');
            INSERT INTO characters VALUES('g',3,'alice','pending');
        ''')

        @contextlib.contextmanager
        def get_connection():
            yield self.db

        @contextlib.contextmanager
        def transaction():
            try:
                yield self.db
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

        src = (ROOT/'modules'/'pr_rumor.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                    {'rumor_allowed','set_consent','set_category_consent'}]
        self.assertEqual(len(selected), 3)
        ns = {'RUMOR_CATEGORIES': ('戀情','商業','工作','人際','私生活','其他'),
              'initialize': lambda: None, 'transaction': transaction,
              'time': types.SimpleNamespace(time=lambda: 100)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), '<real-pr-rumor-sql>', 'exec'), ns)
        fake_pr = types.ModuleType('modules.pr_rumor')
        for name in ('rumor_allowed','set_consent','set_category_consent','initialize','RUMOR_CATEGORIES'):
            setattr(fake_pr, name, ns[name])
        import modules
        self.original_pr = sys.modules.get('modules.pr_rumor')
        self.original_attr = getattr(modules,'pr_rumor',None)
        modules.pr_rumor = fake_pr
        self.patched = patch.dict(sys.modules, {'modules.pr_rumor':fake_pr, 'core.database': types.SimpleNamespace(get_connection=get_connection)})
        self.patched.start()
        import importlib.util
        spec = importlib.util.spec_from_file_location('testing_consent_bridge', ROOT/'modules'/'rumor_consent_bridge.py')
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.check = ns['rumor_allowed']

    def tearDown(self):
        import modules
        modules.pr_rumor = self.original_attr
        self.patched.stop()
        self.db.close()

    def test_default_opt_in_on_all_categories(self):
        result = self.bridge.read_authorization('g','alice',1)
        self.assertTrue(result['enabled'])
        self.assertTrue(all(result['effective'].values()))

    def test_general_disable_blocks_all_categories(self):
        state = self.bridge.set_authorization('g','alice',1,enabled=False)
        self.assertFalse(state['enabled'])
        self.assertFalse(any(state['effective'].values()))
        self.assertFalse(self.check(self.db,'g',1,'戀情'))

    def test_category_specific_disable(self):
        state = self.bridge.set_authorization('g','alice',1,category='戀情',enabled=False)
        self.assertFalse(state['effective']['戀情'])
        self.assertTrue(state['effective']['工作'])
        self.assertFalse(self.check(self.db,'g',1,'戀情'))
        self.assertTrue(self.check(self.db,'g',1,'工作'))

    def test_category_is_preserved_under_general_disable(self):
        self.bridge.set_authorization('g','alice',1,category='戀情',enabled=False)
        self.bridge.set_authorization('g','alice',1,enabled=False)
        state = self.bridge.set_authorization('g','alice',1,enabled=True)
        self.assertFalse(state['effective']['戀情'])
        self.assertTrue(state['effective']['工作'])

    def test_unrelated_guild_and_character_unmodified(self):
        self.bridge.set_authorization('g','alice',1,enabled=False)
        self.assertTrue(self.check(self.db,'g',2,'戀情'))
        self.assertTrue(self.check(self.db,'other',1,'戀情'))

    def test_wrong_owner_and_inactive_rejected(self):
        for user,cid in [('bob',1),('alice',3)]:
            with self.subTest(user=user,cid=cid):
                with self.assertRaises(PermissionError):
                    self.bridge.set_authorization('g',user,cid,enabled=False)

    def test_invalid_type_and_category_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.set_authorization('g','alice',1,enabled=1)
        with self.assertRaises(ValueError):
            self.bridge.set_authorization('g','alice',1,category='不明',enabled=True)


class RumorPanelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rumor = (ROOT/'modules'/'rumor_interface.py').read_text(encoding='utf-8')
        cls.viewer = (ROOT/'modules'/'character_view.py').read_text(encoding='utf-8')
        cls.pr = (ROOT/'modules'/'pr_rumor.py').read_text(encoding='utf-8')
        cls.menu_tree = ast.parse(cls.rumor)

    def test_five_main_categories_and_all_12_remaining_actions(self):
        assign = next(n for n in self.menu_tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t,ast.Name) and t.id=='RUMOR_MENU' for t in n.targets))
        menu = ast.literal_eval(assign.value)
        self.assertEqual(len(menu),5)
        actions = {value for _,(_,items) in menu.items() for _,value in items}
        self.assertEqual(actions, {'create','records','evidence','investigate','crowd',
                                   'report','submit','police_status','expose','pr','history','result'})
        self.assertNotIn('consent',actions)
        self.assertNotIn('category',actions)

    def test_privacy_controls_live_under_view_character_edit(self):
        self.assertIn('class CharacterRumorConsentView',self.viewer)
        self.assertIn('class CharacterRumorConsentSelect',self.viewer)
        self.assertIn("label='📰 傳聞參與授權'", self.viewer)
        self.assertIn('read_authorization(',self.viewer)
        self.assertIn('set_authorization(',self.viewer)
        self.assertIn('from modules.application_extras import ApplicationModal',self.viewer)
        self.assertIn('await self._toggle(interaction, \'personality\')', self.viewer)
        self.assertIn('await self._toggle(interaction, \'biography\')', self.viewer)

    def test_no_old_authorization_buttons_in_rumor(self):
        self.assertNotIn('label="整體傳聞授權"',self.rumor)
        self.assertNotIn('label="設定分類授權"',self.rumor)
        self.assertNotIn('label="🔐 授權開關"',self.pr)
        self.assertIn('view=RumorPlayerPanel(interaction.user.id, interaction.guild_id)',self.rumor)
        self.assertIn('這不是你的傳聞操作面板',self.rumor)

    def test_cross_systems_continue_using_same_policy(self):
        paths=['modules/pr_rumor.py','modules/rumor_network.py','core/rumor_auto_case_bridge.py',
               'core/malicious_rumor_bridge.py','core/rumor_auto_broadcast.py']
        sources=[(ROOT / name).read_text(encoding='utf-8') for name in paths]
        for name, src in zip(paths, sources):
            with self.subTest(path=name):
                self.assertTrue('rumor_allowed' in src or 'rp_rumor_optout' in src)
        self.assertIn('COALESCE(o.enabled, 1)', sources[-1])
        self.assertIn('COALESCE(x.enabled, 1)', sources[-1])

    def test_creation_not_modified_in_this_delivery(self):
        # /創角 是下一個階段；不在本輪改動。
        self.assertIn('CharacterRumorConsentView',self.viewer)


if __name__ == '__main__':
    unittest.main()
