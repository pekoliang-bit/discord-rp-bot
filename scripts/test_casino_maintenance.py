"""Read-only structural regression tests for casino maintenance.

Run from project root: python -m unittest scripts/test_casino_maintenance.py -v
"""
from __future__ import annotations
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / 'modules'

class CasinoMaintenanceTests(unittest.TestCase):
    def test_all_required_modules_exist(self):
        for name in ('casino_maintenance','casino_lottery','casino_effects','casino_commands'):
            self.assertTrue((MOD / f'{name}.py').is_file(), name)
    def test_all_modules_parse(self):
        for name in ('casino_maintenance','casino_lottery','casino_effects','casino_commands'):
            ast.parse((MOD / f'{name}.py').read_text(encoding='utf-8-sig'))
    def test_maintenance_is_not_extension(self):
        tree = ast.parse((MOD / 'casino_maintenance.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(node,ast.AsyncFunctionDef) and node.name=='setup' for node in tree.body))
    def test_maintenance_does_not_create_new_lottery(self):
        source = (MOD/'casino_maintenance.py').read_text(encoding='utf-8')
        self.assertNotIn('create_draw(',source)
        self.assertIn('settle_lottery_tickets(',source)
    def test_one_casino_command(self):
        tree=ast.parse((MOD/'casino_commands.py').read_text(encoding='utf-8'))
        found=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.AsyncFunctionDef):
                for d in node.decorator_list:
                    if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='command':
                        for k in d.keywords:
                            if k.arg=='name' and isinstance(k.value,ast.Constant): found.append(k.value.value)
        self.assertEqual(found.count('å¨æ¨å'),1)
        self.assertNotIn('å¨æ¨åæè¡',found)

if __name__=='__main__':unittest.main(verbosity=2)
