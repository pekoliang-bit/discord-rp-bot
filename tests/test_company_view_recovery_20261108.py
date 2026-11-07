"""公司首頁隔離與角色查看、標籤驗收的離線回歸測試。"""
from __future__ import annotations

import ast
import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CompanyViewRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'modules' / 'company_portal.py').read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def test_company_command_keeps_basic_panel_available_on_data_error(self):
        command = next(n for n in ast.walk(self.tree)
                       if isinstance(n, ast.AsyncFunctionDef) and n.name == 'company')
        text = ast.get_source_segment(self.source, command)
        self.assertIn('公司資料暫時無法同步', text)
        self.assertIn('CompanyPanel(uid, gid, bool(owned))', text)
        self.assertIn('LOG.exception', text)

    def test_company_root_has_no_overcrowded_plugin_buttons(self):
        root = next(n for n in self.tree.body
                    if isinstance(n, ast.ClassDef) and n.name == 'CompanyPanel')
        method_names = {n.name for n in root.body if isinstance(n, ast.AsyncFunctionDef)}
        self.assertIn('management_entry', method_names)
        self.assertEqual(method_names & {
            'management_entry', 'apply', 'leave', 'submit', 'refresh', 'accept_invite'
        }, {'management_entry', 'apply', 'leave', 'submit', 'refresh', 'accept_invite'})
        initializer = next(n for n in root.body
                           if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        initializer_code = ast.get_source_segment(self.source, initializer)
        self.assertNotIn('add_item(', initializer_code)
        self.assertNotIn('CompanyTreasuryButton(', initializer_code)

    def test_management_modules_are_isolated_and_click_guarded(self):
        advanced = next(n for n in self.tree.body
                        if isinstance(n, ast.ClassDef) and n.name == 'CompanyManagementPanel')
        source = ast.get_source_segment(self.source, advanced)
        for name in ('CompanyTreasuryButton', 'BrandManagementButton', 'CompanyVIPButton',
                     'CompanyContractEntry', 'CompanyGroupEntry', 'CompanyCommercialEntry'):
            self.assertIn(name, source)
        self.assertIn('LOG.exception', source)
        self.assertIn('interaction_check', source)

    def test_company_context_new_guild_and_owner(self):
        from core import database
        from modules.company_business import initialize as init_business
        from modules.world_terms import organization_terms
        with tempfile.TemporaryDirectory() as temp:
            original = database.DATABASE_PATH
            database.DATABASE_PATH = str(Path(temp) / 'company.sqlite')
            try:
                database.init_db()
                database.register_guild('83421')
                with database.transaction() as conn:
                    conn.execute('''CREATE TABLE IF NOT EXISTS company_employees
                        (guild_id TEXT, company_id TEXT, character_id INTEGER,
                         rank INTEGER, exp INTEGER, status TEXT)''')
                    conn.execute('''CREATE TABLE IF NOT EXISTS company_job_positions
                        (guild_id TEXT, company_id TEXT, rank INTEGER, title TEXT,
                         exp_required INTEGER, promotion_mode TEXT)''')
                init_business()
                # Extract the actual repository function without importing discord.py.
                context = next(n for n in self.tree.body
                               if isinstance(n, ast.FunctionDef) and n.name == 'company_context')
                scope = {
                    'init_business': init_business,
                    'get_connection': database.get_connection,
                    'organization_terms': organization_terms,
                }
                exec(compile(ast.Module(body=[context], type_ignores=[]),
                             'company_context.py', 'exec'), scope)
                lookup = scope['company_context']
                self.assertEqual(lookup('83421', '11'), ([], ''))
                with database.transaction() as conn:
                    conn.execute('''INSERT INTO rp_approved_company_authorizations
                        (guild_id, company_id, original_applicant_id, status)
                        VALUES ('83421', 'COMP-1', '11', 'approved')''')
                owned, employees = lookup('83421', '11')
                self.assertEqual(owned, ['COMP-1'])
                self.assertEqual(employees, '')
                self.assertEqual(lookup('83421', '12'), ([], ''))
            finally:
                database.DATABASE_PATH = original

    def test_character_view_does_not_reference_deleted_button(self):
        source = (ROOT / 'modules' / 'character_view.py').read_text(encoding='utf-8')
        self.assertNotIn('self.remove_item(self.like_button)', source)

    def test_catalog_test_uses_real_chinese_headers(self):
        source = (ROOT / 'tests' / 'test_impression_tag_v21_20261107.py').read_text(encoding='utf-8')
        self.assertIn("['標籤名稱']", source)
        self.assertNotIn('æ¨', source)


if __name__ == '__main__':
    unittest.main()
