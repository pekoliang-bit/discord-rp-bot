import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from core.turso_backend import Connection, driver_call
from scripts.verify_turso_migration import compare


class ErrorMigrationTests(unittest.TestCase):
    def test_driver_errors(self):
        for msg in ['SQLite error: UNIQUE constraint failed: t.id', 'SQLITE_CONSTRAINT_CHECK', 'CHECK constraint failed: n>=0', 'NOT NULL constraint failed: t.n', 'FOREIGN KEY constraint failed']:
            raw = Mock(side_effect=ValueError(msg))
            with self.assertRaises(sqlite3.IntegrityError):
                driver_call(raw)
            raw.assert_called_once()
        with self.assertRaises(sqlite3.OperationalError):
            driver_call(Mock(side_effect=ValueError('database is locked')))
        with self.assertRaises(ValueError):
            driver_call(Mock(side_effect=ValueError('invalid column type')))
        with self.assertRaises(ValueError):
            driver_call(Mock(side_effect=ValueError('network disconnected')))

    def test_cursor_and_commit(self):
        raw = Mock()
        raw.cursor.return_value.execute.side_effect = ValueError('SQLITE_CONSTRAINT_UNIQUE')
        raw.commit.side_effect = ValueError('FOREIGN KEY constraint failed')
        db = Connection(raw)
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute('INSERT INTO t VALUES(1)')
        with self.assertRaises(sqlite3.IntegrityError):
            db.commit()
        raw.commit.assert_called_once()

    def test_migration(self):
        with tempfile.TemporaryDirectory() as d:
            a,b = [Path(d)/n for n in ['old.db','export.db']]
            for p, rows in [(a, [(1,'a'),(2,'b')]),(b,[(2,'b'),(1,'a')])]:
                with sqlite3.connect(p) as db:
                    db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)')
                    db.executemany('INSERT INTO t VALUES(?,?)',rows)
            self.assertEqual(compare(a,b)['rows'],2)
            with sqlite3.connect(b) as db:
                db.execute("UPDATE t SET v='c' WHERE id=1")
            with self.assertRaisesRegex(RuntimeError,'Migration mismatch'):
                compare(a,b)
