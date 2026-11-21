import sqlite3
import unittest
from unittest.mock import patch
from core.turso_backend import Connection, credentials, require_staging_gate, configured


class CandidateTests(unittest.TestCase):
    def test_rows_and_rollback(self):
        db = Connection(sqlite3.connect(':memory:'))
        with db:
            db.executescript('CREATE TABLE a(id INTEGER PRIMARY KEY, value TEXT); INSERT INTO a VALUES(1, \'a;b\');')
        row = db.execute('SELECT * FROM a').fetchone()
        self.assertEqual(dict(row), {'id': 1, 'value': 'a;b'})
        self.assertEqual(row['VALUE'], 'a;b')
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.executescript("UPDATE a SET value='changed';")
                raise ValueError('rollback')
        except ValueError:
            pass
        self.assertEqual(db.execute('SELECT value FROM a').fetchone()[0], 'a;b')
        db.close()

    def test_fail_closed(self):
        with patch.dict('os.environ', {'RENDER':'true'}, clear=True):
            self.assertTrue(configured())
            with self.assertRaises(RuntimeError):
                credentials()
        with patch.dict('os.environ', {'TURSO_DATABASE_URL':'libsql://example.invalid', 'TURSO_AUTH_TOKEN':'test'}, clear=True):
            with self.assertRaises(RuntimeError):
                require_staging_gate()


if __name__ == '__main__':
    unittest.main()
