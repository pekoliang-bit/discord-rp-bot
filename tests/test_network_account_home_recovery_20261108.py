"""Regression checks for network account choice and the role-account home panel.

No Discord token or production database is used.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class AccountHomeTests(unittest.TestCase):
    def test_account_home_buttons_have_valid_emoji_literals(self):
        source = (ROOT / 'modules/network_group_ui.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        home = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'NetworkHome')
        labels = []
        for method in home.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            for decorator in method.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != 'button':
                    continue
                keys = {item.arg: ast.literal_eval(item.value) for item in decorator.keywords}
                self.assertNotEqual(keys.get('emoji'), '@', 'ã@ãä¸æ¯ææç Discord æé emoji')
                labels.append(keys['label'])
        self.assertEqual(len(labels), 5)
        self.assertIn('å¸³èè³è¨', labels)

    def test_picker_acknowledges_first_and_separates_db_from_ui_error(self):
        source = (ROOT / 'modules/network_group_ui.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        picker = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'AccountPicker')
        callback = next(node for node in ast.walk(picker)
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chosen')
        self.assertIn('await interaction.response.defer', ast.unparse(callback.body[0]))
        body = ast.unparse(callback)
        self.assertIn('ç¶²è·¯å¸³èè³æåº«è®åå¤±æ', body)
        self.assertIn('ç¶²è·¯é¦é  Discord è¨æ¯ç¼éå¤±æ', body)
        self.assertIn('ç¶²è·¯é¦é å»ºç«å¤±æ', body)
        self.assertIn("account['character_id']", body)

    def test_existing_active_character_accounts_are_readable(self):
        from core import database
        from core import network_groups as ng
        from core.network_account_autocreate import ensure_character_accounts

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(database, 'DATABASE_PATH', Path(directory) / 'network.sqlite'):
                database.init_db()
                with database.transaction() as connection:
                    connection.execute("INSERT INTO guilds(guild_id,name) VALUES('889','testing')")
                    connection.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(7,'889','44','è§è²ç²','active')")
                    connection.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(8,'889','55','è§è²ä¹','active')")
                ids = ensure_character_accounts('889', 7)
                self.assertEqual(len(ids), 2)
                for account in ng.accounts('889', '44', 7):
                    row = ng.account_info('889', '44', account['id'])
                    self.assertEqual(row['character_id'], 7)
                    self.assertEqual(row['character_name'], 'è§è²ç²')
                    self.assertIn(row['account_kind'], ('personal', 'official'))
                    with self.assertRaises(PermissionError):
                        ng.account_info('889', '55', account['id'])
                self.assertEqual(ensure_character_accounts('889', 7), ids)


if __name__ == '__main__':
    unittest.main()
