"""獨立 SQLite 測試：不得連線正式玩家資料。"""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
core = types.ModuleType('core')
core.__path__ = [str(ROOT / 'core')]
dbmodule = types.ModuleType('core.database')
@contextmanager
def transaction():
    conn = sqlite3.connect(DBPATH)
    conn.row_factory = sqlite3.Row
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_connection():
    conn = sqlite3.connect(DBPATH)
    conn.row_factory = sqlite3.Row
    return conn

dbmodule.transaction = transaction
dbmodule.get_connection = get_connection
paid = types.ModuleType('core.rumor_pr_paid_flow')
def initialize_pr_strategy_config(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_rumor_pr_strategy_config(
        guild_id TEXT, strategy TEXT, base_score INTEGER, enabled INTEGER,
        PRIMARY KEY(guild_id,strategy))''')
paid.initialize_pr_strategy_config = initialize_pr_strategy_config
with patch.dict(sys.modules, {'core': core,'core.database': dbmodule,
                              'core.rumor_pr_paid_flow': paid}):
    import importlib.util
    spec = importlib.util.spec_from_file_location('pr_admin_target', ROOT/'core'/'rumor_pr_strategy_admin.py')
    target = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = target
    spec.loader.exec_module(target)

class TestPRConfig(unittest.TestCase):
    def setUp(self):
        global DBPATH
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        DBPATH = str(Path(tmp.name)/'test.sqlite')
        with transaction() as db:
            target.initialize(db)
    def test_empty_on_initialize(self):
        self.assertEqual(target.list_strategies('g'), [])
    def test_permission(self):
        with self.assertRaises(PermissionError):
            target.update_strategy('g','澄清',100,True,'owner')
    def test_versions_and_noop(self):
        x = target.update_strategy('g','澄清',100,True,'owner',trusted=True)
        self.assertEqual(x.version,1)
        y = target.update_strategy('g','澄清',100,True,'owner',trusted=True)
        self.assertEqual(y.version,1)
        z = target.update_strategy('g','澄清',120,False,'owner',trusted=True)
        self.assertEqual(z.version,2)
        self.assertFalse(target.list_strategies('g')[0].enabled)
    def test_invalid_value(self):
        with self.assertRaises(ValueError):
            target.update_strategy('g','澄清',True,True,'owner',trusted=True)

if __name__ == '__main__':
    unittest.main()
