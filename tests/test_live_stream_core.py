"""Regression tests for the /網路 session core and integration surfaces.
Run with: python -m unittest discover -s tests -p test_live_stream_core.py -v
Uses a private temporary database. No Discord token required.
"""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from core import config, database, live_stream
from core import live_social, live_pk, live_commerce


class LiveStreamCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(config, 'DATABASE_PATH', os.path.join(self.tmp.name, 'test.sqlite3'))
        self.path_patch.start()
        # database imported DATABASE_PATH by value.
        self.db_patch = patch.object(database, 'DATABASE_PATH', config.DATABASE_PATH)
        self.db_patch.start()
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('123','test')")
            for uid in (101, 102, 103):
                db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('123',?,?, 'active')", (str(uid), str(uid)))

    def tearDown(self):
        self.db_patch.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_open_close_and_owner_only(self):
        sid = live_stream.start(123,101,1,'First','personal','realtime',now=100)
        self.assertEqual(live_stream.current(123,1,now=101)['id'],sid)
        with self.assertRaises(ValueError):
            live_stream.start(123,101,1,'Again','personal','realtime',now=102)
        with self.assertRaises(PermissionError):
            live_stream.stop(123,102,sid,now=103)
        self.assertEqual(live_stream.stop(123,101,sid,now=104)['status'],'ended')
        self.assertIsNone(live_stream.current(123,1,now=105))

    def test_fixed_duration_expiry(self):
        live_stream.start(123,101,1,'Fixed','personal','fixed',15,now=100)
        self.assertIsNotNone(live_stream.current(123,1,now=999))
        self.assertIsNone(live_stream.current(123,1,now=1000))

    def test_invitation_and_member_current(self):
        sid = live_stream.start(123,101,1,'Group','group','realtime',now=100)
        self.assertEqual(live_social.invite(123,sid,101,2,now=101),'102')
        self.assertEqual(live_social.respond(123,1,102,2,True,now=102),'accepted')
        self.assertEqual(live_stream.current(123,2,now=103)['id'],sid)

    def test_pk_and_sales_schema(self):
        a = live_stream.start(123,101,1,'A','pk','realtime',now=100)
        b = live_stream.start(123,102,2,'B','pk','realtime',now=100)
        match = live_pk.challenge(123,101,a,b,now=105)
        self.assertEqual(live_pk.respond(123,102,2,match,True,now=106),'active')
        live_stream.stop(123,101,a,now=107)
        sid=live_stream.start(123,101,1,'Sales','sales','realtime',now=108)
        offer=live_commerce.propose_offer(123,101,sid,'sku-1','item',100,2,now=109)
        self.assertEqual(live_commerce.offers(123,sid)[0]['id'],offer)

    def test_unauthorized_start_and_bad_duration(self):
        with self.assertRaises(PermissionError):
            live_stream.start(123,999,1,'X','personal','realtime')
        with self.assertRaises(ValueError):
            live_stream.start(123,101,1,'X','personal','fixed',100)
        with self.assertRaises(PermissionError):
            live_stream.start(123,101,1,'Official','official','realtime')

if __name__ == '__main__':
    unittest.main()
