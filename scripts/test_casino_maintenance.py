"""娛樂城彩票維護靜態回歸測試。

執行：python -m unittest discover -s scripts -p 'test_casino_maintenance.py' -v
不連接正式資料庫，不連 Discord，不修改玩家金幣。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'modules'
REQUIRED = ('casino_maintenance', 'casino_lottery', 'casino_effects', 'casino_commands')


class CasinoMaintenanceTests(unittest.TestCase):
    def test_all_required_modules_exist(self):
        for name in REQUIRED:
            with self.subTest(module=name):
                self.assertTrue((MOD / f'{name}.py').is_file())

    def test_all_modules_parse(self):
        for name in REQUIRED:
            with self.subTest(module=name):
                ast.parse((MOD / f'{name}.py').read_text(encoding='utf-8-sig'))

    def test_maintenance_is_not_extension(self):
        tree = ast.parse((MOD / 'casino_maintenance.py').read_text(encoding='utf-8-sig'))
        self.assertFalse(any(isinstance(node, ast.AsyncFunctionDef) and node.name == 'setup' for node in tree.body))

    def test_maintenance_does_not_create_new_lottery(self):
        tree = ast.parse((MOD / 'casino_maintenance.py').read_text(encoding='utf-8-sig'))
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn('create_draw', calls)
        self.assertIn('settle_lottery_tickets', calls)

    def test_one_casino_command(self):
        tree = ast.parse((MOD / 'casino_commands.py').read_text(encoding='utf-8-sig'))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == 'command':
                        for kw in dec.keywords:
                            if kw.arg == 'name' and isinstance(kw.value, ast.Constant):
                                found.append(kw.value.value)
        self.assertEqual(found.count('娛樂城'), 1)
        self.assertNotIn('娛樂城排行', found)

    def test_maintenance_entry_exists(self):
        tree = ast.parse((MOD / 'casino_maintenance.py').read_text(encoding='utf-8-sig'))
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn('process_due_lotteries', functions)


if __name__ == '__main__':
    unittest.main(verbosity=2)
