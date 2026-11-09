"""新版本介面隱私與減少重複入口的靜態合約。"""
import ast
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class UiCleanupTests(unittest.TestCase):
    def src(self, path):
        txt=(ROOT/path).read_text(encoding='utf-8-sig')
        ast.parse(txt,filename=path)
        return txt
    def test_lottery_no_id_argument(self):
        s=self.src('modules/lottery_threads.py')
        self.assertIn('async def draw(self, i: discord.Interaction):',s)
        self.assertIn('NewcomerRoleSelect',s)
        self.assertIn("status='active'",s)
    def test_malicious_name_and_link(self):
        s=self.src('modules/malicious_actions_ui.py')
        self.assertIn('目標角色名字: str | None',s)
        self.assertIn('玩家連結: str | None',s)
        self.assertIn("AND name=? AND status='active'",s)
        self.assertNotIn('目標角色編號: int | None',s)
    def test_rumors_clean(self):
        s=self.src('modules/rumor_auto_broadcast_ui.py')
        self.assertIn("body = f'📰 **娛樂圈傳聞**",s)
        self.assertNotIn('未經證實**',s)
        self.assertNotIn('||{marker}||',s)
    def test_company_root_not_monkey_patched(self):
        for path in ('modules/endorse.py','modules/endorse_life.py','modules/endorse_rules.py','modules/renew.py'):
            self.assertNotIn('CompanyPanel.__init__ =',self.src(path))
        self.assertIn('CompanyCommercialCategoryView',self.src('modules/company_commercial_ui.py'))

class TargetResolverTests(unittest.TestCase):
    def test_name_and_profile_must_match_same_owner(self):
        import sqlite3
        import tempfile
        import contextlib
        import types
        from unittest.mock import patch
        from core import database
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'roles.sqlite3')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE characters (guild_id TEXT, owner_id TEXT, id INTEGER, name TEXT, status TEXT)')
                db.executemany('INSERT INTO characters VALUES (?,?,?,?,?)', [
                    ('guild','123456789012345678',1,'小雪','active'),
                    ('guild','999999999999999999',2,'小雪','active'),
                    ('guild','123456789012345678',3,'星星','retired'),
                ])
            with patch.object(database,'DATABASE_PATH',path):
                import re
                from core.database import get_connection
                src=(ROOT/'modules/malicious_actions_ui.py').read_text(encoding='utf-8')
                tree=ast.parse(src)
                fun=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='resolve_malicious_target')
                pattern=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_PLAYER_LINK' for t in n.targets))
                namespace={'re':re,'get_connection':get_connection,'__builtins__':__builtins__}
                exec(compile(ast.Module(body=[pattern,fun],type_ignores=[]),'<resolver-test>','exec'),namespace)
                resolve_malicious_target=namespace['resolve_malicious_target']
                self.assertEqual(resolve_malicious_target('guild','小雪','<@123456789012345678>'),1)
                self.assertEqual(resolve_malicious_target('guild','小雪','https://discord.com/users/999999999999999999'),2)
                with self.assertRaises(LookupError):
                    resolve_malicious_target('guild','星星','<@123456789012345678>')
                with self.assertRaises(ValueError):
                    resolve_malicious_target('guild','小雪','https://discord.com/channels/123/456')
