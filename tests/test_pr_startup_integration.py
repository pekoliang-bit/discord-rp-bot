"""真實 SQLite 公關啟動與保留資料驗收。

從專案根目錄執行：python -m unittest discover -s tests -p 'test_pr_startup_integration.py' -v
需先安裝最新公關 core 模組以及這批的 core/database.py。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database
from core.rumor_pr_strategy_admin import update_strategy, list_strategies
from core.rumor_pr_paid_flow import initialize_pr_strategy_config
from core.rumor_pr_effects import initialize_pr_effects


class PRStartupIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'rp-test.sqlite3'
        self.path_patch = patch.object(database, 'DATABASE_PATH', self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        database.init_db()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def test_required_pr_tables_exist(self):
        with self.connect() as db:
            found = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {'rp_rumor_pr_strategy_config', 'rp_rumor_pr_strategy_audit',
                    'rp_rumor_pr_effects', 'rp_rumor_grade_snapshots'}
        self.assertTrue(expected <= found, expected - found)

    def test_no_unapproved_strategy_seed(self):
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_rumor_pr_strategy_config').fetchone()[0], 0)

    def test_repeated_startup_preserves_strategy(self):
        item = update_strategy('guild1', '公開澄清', 123, True, 'owner1', trusted=True)
        self.assertEqual(item.version, 1)
        database.init_db()
        rows = list_strategies('guild1')
        self.assertEqual([(r.strategy,r.base_score,r.enabled,r.version) for r in rows],
                         [('公開澄清',123,True,1)])
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM rp_rumor_pr_strategy_audit').fetchone()[0], 1)

    def test_grade_schema_supports_snapshot(self):
        with self.connect() as db:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(rp_rumor_grade_snapshots)')}
        self.assertEqual(cols, {'guild_id','rumor_id','character_id','payload','fingerprint','created_at'})

    def test_initializers_are_idempotent(self):
        with self.connect() as db:
            initialize_pr_strategy_config(db)
            initialize_pr_effects(db)
            db.commit()
        database.init_db()


if __name__ == '__main__':
    unittest.main()
