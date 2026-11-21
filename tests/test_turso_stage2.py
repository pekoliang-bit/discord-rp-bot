import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from core import database
from core.turso_backend import Connection
from scripts import verify_turso_schema as stage

class Stage2Tests(unittest.TestCase):
    def test_schema_rollback_and_nonempty_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.sqlite3'
            def connect():
                db = Connection(sqlite3.connect(path))
                db.execute('PRAGMA foreign_keys=ON')
                return db
            with patch.dict(os.environ, {'RP_TURSO_MODE':'staging','TURSO_DATABASE_URL':'libsql://test.invalid','TURSO_AUTH_TOKEN':'test'}, clear=True), patch.object(stage,'connect',connect), patch.object(database,'get_connection',connect):
                stage.verify_schema()
                stage.verify_ledger_rollback()
                stage.verify_schema()
                db = connect()
                db.execute("INSERT INTO guilds(guild_id) VALUES ('existing')")
                db.commit()
                db.close()
                with self.assertRaisesRegex(RuntimeError,'not empty'):
                    stage.verify_schema()

if __name__ == '__main__':
    unittest.main()
