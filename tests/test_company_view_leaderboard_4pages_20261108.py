
"""Regression contracts for company layout, character pages and safe leaderboard activation."""
from __future__ import annotations
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def source(name):return (ROOT/name).read_text(encoding='utf-8-sig')

def buttons(cls):
    result={}
    for node in cls.body:
        if isinstance(node,ast.AsyncFunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec,ast.Call) and any(k.arg=='label' for k in dec.keywords):
                    kw={k.arg:k.value for k in dec.keywords}
                    result[ast.literal_eval(kw['label'])]=ast.literal_eval(kw['row'])
    return result

class ContractTests(unittest.TestCase):
    def test_company_main_has_compact_rows(self):
        tree=ast.parse(source('modules/company_portal.py'))
        cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='CompanyPanel')
        rows=buttons(cls)
        self.assertEqual(len(rows),3)
        self.assertEqual([sum(v==r for v in rows.values()) for r in range(3)],[2,1,0])
        self.assertIn('👔 員工與任職',rows)
        self.assertIn('🏢 公司管理功能',rows)
        self.assertIn('⬅️ 返回公司首頁',source('modules/company_portal.py'))
    def test_profile_four_pages(self):
        src=source('modules/character_view.py')
        tree=ast.parse(src)
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='_character_detail_max_page')
        self.assertIn('return 3',ast.get_source_segment(src,fn))
        for title in ('🔒 私人編號', '📋 基本資料', '📅 工作檔期／倒數',
                      '🏷️ 大眾印象', '💰 貨幣資產', '🚗 車子', '🏠 房產',
                      '🏢 公司／產業', '📝 備註'):
            self.assertIn(title,src)
        self.assertIn('visible_biography(settings, private and authorized)',src)
    def test_ranking_does_not_release_payout_automatically(self):
        ui=source('modules/leaderboard_ui.py')
        self.assertIn('enable_settlement',ui)
        self.assertIn('interaction.guild.owner_id',ui)
        self.assertIn('await interaction.response.defer',ui)
        policy=source('modules/leaderboard_payout_policy.py')
        self.assertIn('PAYOUT_ENABLED = False',policy)
        self.assertIn('14天小結算不能派發獎勵',source('modules/leaderboard_payout_preview.py'))
        self.assertIn('leaderboard_runtime',source('main.py')+'leaderboard_runtime')

class ConfigTests(unittest.TestCase):
    def test_guild_scoping_and_idempotent_start(self):
        import core.database as db
        from modules.leaderboard_config import configure,get_config
        with tempfile.TemporaryDirectory() as d:
            with patch.object(db,'DATABASE_PATH',str(Path(d)/'lb.sqlite')):
                db.init_db()
                configure('100','2026-11-08T00:00:00+00:00',True)
                cfg=get_config('100')
                self.assertEqual(cfg['anchor_utc'],'2026-11-08T00:00:00+00:00')
                self.assertEqual(cfg['enabled'],1)
                self.assertIsNone(get_config('200'))
                configure('100','2026-11-08T00:00:00+00:00',True)
                with self.assertRaises(ValueError):
                    configure('100','2026-11-09T00:00:00+00:00',True)
                from modules.leaderboard_payout_policy import PAYOUT_ENABLED
                self.assertFalse(PAYOUT_ENABLED)

if __name__=='__main__':unittest.main()
