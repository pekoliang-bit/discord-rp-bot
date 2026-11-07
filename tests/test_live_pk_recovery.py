"""PK invitation slot recovery and visibility regressions. No Discord connection."""
import os
import tempfile
import unittest
from unittest.mock import patch

from core import config, database, live_pk, live_stream


class LivePKRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'pk.sqlite3')
        self.config_patch = patch.object(config, 'DATABASE_PATH', self.path)
        self.database_patch = patch.object(database, 'DATABASE_PATH', self.path)
        self.config_patch.start()
        self.database_patch.start()
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('99','Live PK Tests')")
            for index in (1, 2, 3):
                db.execute(
                    "INSERT INTO characters(guild_id,owner_id,name,status) "
                    "VALUES(?,?,?,'active')",
                    ('99', str(index), 'host-' + str(index)),
                )

    def tearDown(self):
        self.database_patch.stop()
        self.config_patch.stop()
        self.tmp.cleanup()

    def _start(self, index, *, duration='realtime', now=100):
        minutes = 15 if duration == 'fixed' else None
        return live_stream.start('99', str(index), index, 'PK', 'pk', duration, minutes, now=now)

    def test_expired_invite_releases_slots_for_rematch(self):
        a, b = self._start(1), self._start(2)
        old = live_pk.challenge('99', '1', a, b, now=101)
        newer = live_pk.challenge('99', '1', a, b, now=3702)
        self.assertNotEqual(old, newer)
        with database.get_connection() as db:
            result = db.execute('SELECT status FROM rp_live_pk_matches WHERE id=?', (old,)).fetchone()
            self.assertEqual(result['status'], 'expired')
        self.assertEqual(len(live_pk.pending('99', '2', 2, now=3703)), 1)

    def test_ended_challenger_hidden_from_pending(self):
        a, b = self._start(1), self._start(2)
        live_pk.challenge('99', '1', a, b, now=101)
        live_stream.stop('99', '1', a, now=102)
        self.assertEqual(live_pk.pending('99', '2', 2, now=103), [])
        c = self._start(3, now=103)
        new = live_pk.challenge('99', '3', c, b, now=104)
        self.assertEqual(len(live_pk.pending('99', '2', 2, now=105)), 1)
        self.assertEqual(live_pk.pending('99', '2', 2, now=105)[0]['id'], new)

    def test_expired_challenger_hidden_without_mutating_history(self):
        a = self._start(1, duration='fixed')
        b = self._start(2)
        live_pk.challenge('99', '1', a, b, now=101)
        self.assertEqual(len(live_pk.pending('99', '2', 2, now=999)), 1)
        self.assertEqual(live_pk.pending('99', '2', 2, now=1000), [])


if __name__ == '__main__':
    unittest.main()
