"""正式開群/網路選單/排行榜派獎安全性回歸測試。"""
from __future__ import annotations
import ast
import csv
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class LaunchAndNetworkTests(unittest.TestCase):
    def test_all_python_files_parse(self):
        for file in ROOT.rglob('*.py'):
            with self.subTest(file=str(file.relative_to(ROOT))):
                ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))

    def test_network_account_picker_defers_before_db(self):
        text = (ROOT/'modules/network_group_ui.py').read_text()
        tree = ast.parse(text)
        picker = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name=='AccountPicker')
        init = next(x for x in picker.body if isinstance(x, ast.FunctionDef) and x.name=='__init__')
        cb = next(x for x in ast.walk(init) if isinstance(x, ast.AsyncFunctionDef) and x.name=='chosen')
        self.assertIn('await interaction.response.defer',ast.unparse(cb.body[0]))
        self.assertIn('interaction.followup.send',ast.unparse(cb))
        self.assertNotIn('interaction.response.send_message',ast.unparse(cb))

    def test_network_character_picker_defers_before_db(self):
        tree=ast.parse((ROOT/'modules/network_group_ui.py').read_text())
        picker=next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='CharacterPicker')
        cb=next(n for n in ast.walk(picker) if isinstance(n,ast.AsyncFunctionDef) and n.name=='chosen')
        self.assertIn('await interaction.response.defer',ast.unparse(cb.body[0]))

    def test_account_modal_defers_before_db(self):
        tree=ast.parse((ROOT/'modules/network_group_ui.py').read_text())
        modal=next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name=='CreateAccountModal')
        cb=next(n for n in modal.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='on_submit')
        self.assertIn('await interaction.response.defer',ast.unparse(cb.body[0]))

    def test_launch_button_only_owner(self):
        content=(ROOT/'modules/official_center.py').read_text()
        self.assertIn('if is_owner(interaction):\n            self.add_item(LaunchAuditButton())',content)
        self.assertIn('require_owner(interaction)',content)
        self.assertIn("label='🚪 正式開群'",content)

    def test_launch_with_missing_commands_blocks(self):
        from modules.official_launch_readiness import scan
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        result=scan('test',{'創角'},database=db)
        self.assertFalse(result['passed'])
        self.assertTrue(any(x['title']=='玩家指令入口' for x in result['blocking']))
        self.assertFalse(any(x['title']=='抽獎及劇情' for x in result['blocking']))
        db.close()

    def test_does_not_modify_database(self):
        from modules.official_launch_readiness import scan
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE characters (id INTEGER)')
        before=db.total_changes
        scan('test',[],database=db)
        self.assertEqual(db.total_changes,before)
        db.close()

    def test_actual_internet_comments_960(self):
        import json
        from collections import Counter
        files = sorted((ROOT/'assets/internet_comment_library').glob('comments_part_*.json'))
        comments = [c for f in files for c in json.loads(f.read_text())['approved']]
        counter = Counter(int(c['category_id']) for c in comments if c['status']=='approved')
        self.assertEqual(sum(counter.values()), 960)
        self.assertEqual(len(counter), 12)
        self.assertTrue(all(n >= 80 for n in counter.values()))

    def test_shop_content_is_guild_scoped(self):
        from modules.official_launch_readiness import scan, REQUIRED_COMMANDS
        db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
        for t in ('characters','attributes','accounts'):
            db.execute(f'CREATE TABLE {t} (id INTEGER)')
        db.execute('CREATE TABLE shop_products (guild_id TEXT, active INTEGER, stock INTEGER)')
        db.execute("INSERT INTO shop_products VALUES('unrelated',1,100)")
        r=scan('target', REQUIRED_COMMANDS, database=db)
        self.assertTrue(any(c['title']=='商城實際商品' and not c['passed'] for c in r['checks']))
        db.close()

    def test_tag_catalog_162(self):
        with (ROOT/'assets/impression_tags/catalog_v2_1.csv').open(encoding='utf-8-sig') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),162)
        self.assertTrue(all(r['效果審核狀態']=='已核准' for r in rows))

    def test_leaderboard_awards_still_blocked(self):
        from modules.leaderboard_go_live_audit import audit
        report=audit()
        self.assertEqual(report['boards'],28)
        self.assertFalse(report['ready_to_enable'])
        self.assertGreater(report['blocked_boards'],0)

    def test_enable_settlement_not_same_as_awards(self):
        src=(ROOT/'modules/leaderboard_ui.py').read_text()
        self.assertIn('configure, get_config',src)
        self.assertIn('獎勵發放檢查',src)
        self.assertNotIn('PAYOUT_ENABLED = True',src)


if __name__=='__main__': unittest.main()
