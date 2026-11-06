"""Audit cross-checks the sealed 30d snapshot using an isolated database."""
import contextlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from modules import leaderboard_payout_audit as audit

@contextlib.contextmanager
def db_connection(conn):
    yield conn

class AuditTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
          CREATE TABLE lb_v11_awards(guild_id TEXT,cycle TEXT,idx INTEGER,board TEXT,entity_key TEXT,rank INTEGER,reward_json TEXT,state TEXT);
          CREATE TABLE lb_v11_periods(guild_id TEXT,cycle TEXT,idx INTEGER,state TEXT);
          CREATE TABLE lb_v11_snapshots(guild_id TEXT,cycle TEXT,idx INTEGER,board TEXT,entity_key TEXT,rank INTEGER);
          CREATE TABLE characters(guild_id TEXT,id INTEGER,status TEXT,settings_json TEXT);
        """)
        self.db.execute("INSERT INTO characters VALUES ('1',1,'active','{}')")
        self.db.execute("INSERT INTO lb_v11_periods VALUES('1','30d',1,'snapshotted')")
        self.db.execute("INSERT INTO lb_v11_snapshots VALUES('1','30d',1,'heat','lbentity:character:1:1',1)")
        reward=dict(board='heat',rank=1,coins=100,values={'heat':10},requires_adapter=True,reward_version='v11-approved')
        self.db.execute("INSERT INTO lb_v11_awards VALUES(?,?,?,?,?,?,?,?)",('1','30d',1,'heat','lbentity:character:1:1',1,json.dumps(reward),'pending_adapter'))
        self.mock=patch.object(audit,'get_connection', lambda: db_connection(self.db));self.mock.start()

    def tearDown(self):
        self.mock.stop();self.db.close()

    def test_sealed_snapshot_matches(self):
        report=audit.audit_guild('1')
        self.assertEqual(report['ready_for_review'],1)
        self.assertEqual(report['issues'],[])

    def test_rank_tamper_blocked(self):
        self.db.execute('UPDATE lb_v11_snapshots SET rank=2')
        self.assertIn('å¿«ç§','ï¼'.join(audit.audit_guild('1')['issues'][0]['reasons']))

    def test_unsealed_blocked(self):
        self.db.execute("UPDATE lb_v11_periods SET state='pending'")
        self.assertIn('å°å­','ï¼'.join(audit.audit_guild('1')['issues'][0]['reasons']))

    def test_provisional_blocked(self):
        self.db.execute("UPDATE lb_v11_awards SET reward_json=replace(reward_json,'v11-approved','v11-provisional')")
        self.assertEqual(audit.audit_guild('1')['ready_for_review'],0)

    def test_missing_snapshot_blocked(self):
        self.db.execute('DELETE FROM lb_v11_snapshots')
        self.assertIn('å¿«ç§','ï¼'.join(audit.audit_guild('1')['issues'][0]['reasons']))

if __name__ == '__main__': unittest.main()
