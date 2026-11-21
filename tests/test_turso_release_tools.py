import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import verify_turso_release as release
from scripts.verify_turso_backup import inspect_file
from core.turso_backend import Connection


class ReleaseTools(unittest.TestCase):
    def test_snapshot_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.db'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE probe(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO probe VALUES(1)')
            db.commit(); db.close()
            before=path.read_bytes()
            report=inspect_file(path)
            self.assertEqual(report['tables']['probe']['rows'],1)
            self.assertEqual(before,path.read_bytes())

    def test_marker_and_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'probe.db'
            def open_db():
                return Connection(sqlite3.connect(path))
            with patch.object(release,'connect',open_db):
                release.persistence_probe()
                release.persistence_probe()
                release.constraint_probe()
                db=open_db()
                self.assertEqual(db.execute('SELECT COUNT(*) FROM '+release.MARKER).fetchone()[0],1)
                db.close()


if __name__=='__main__':
    unittest.main()
