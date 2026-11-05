
"""Regression checks: a fixed-duration broadcast cannot accept new activity after expiry."""

import os
import tempfile
import unittest

from unittest.mock import patch

from core import (
    config,
    database,
    live_stream,
    live_social,
    live_commerce,
    live_pk,
)


class LiveExpirationGuardsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

        self.cfg = patch.object(
            config,
            'DATABASE_PATH',
            os.path.join(self.tmp.name, 'test.sqlite3')
        )
        self.cfg.start()

        self.dbp = patch.object(
            database,
            'DATABASE_PATH',
            config.DATABASE_PATH
        )
        self.dbp.start()

        database.init_db()

        with database.transaction() as db:
            db.execute(
                "INSERT INTO guilds(guild_id,name) VALUES('123','test')"
            )

            live_social._init(db)
            live_commerce._init(db)
            live_pk._init(db)

            for uid, name, occupation in [
                (11, 'actor', 'æ¼å¡'),
                (22, 'guest', 'æ­æ'),
                (33, 'donor', 'æ¨¡ç¹å')
            ]:
                db.execute(
                    """INSERT INTO characters(
                        guild_id,owner_id,name,occupation,status
                    ) VALUES(?,?,?,?, 'active')""",
                    ('123', str(uid), name, occupation)
                )

    def tearDown(self):
        self.dbp.stop()
        self.cfg.stop()
        self.tmp.cleanup()

    def test_gift_rejected_at_expiry_with_no_ledger_side_effect(self):
        sid = live_stream.start(
            123, 11, 1, 'show',
            'personal', 'fixed', 15,
            now=100
        )

        with self.assertRaisesRegex(ValueError, 'çµæ'):
            live_social.gift(
                123,
                sid,
                33,
                3,
                'æå',
                'expired-gift',
                now=1000
            )

        with database.get_connection() as db:
            self.assertEqual(
                db.execute(
                    """SELECT COUNT(*) FROM ledger_transactions
                       WHERE guild_id='123'"""
                ).fetchone()[0],
                0
            )
            self.assertEqual(
                db.execute(
                    'SELECT COUNT(*) FROM rp_live_gifts'
                ).fetchone()[0],
                0
            )

    def test_group_invite_and_accept_rejected_after_expiry(self):
        sid = live_stream.start(
            123, 11, 1, 'group',
            'group', 'fixed', 15,
            now=100
        )

        live_social.invite(123, sid, 11, 2, now=101)

        self.assertEqual(
            live_social.pending_for_character(
                123, 22, 2, now=999
            )[0]['session_id'],
            sid
        )

        self.assertEqual(
            live_social.pending_for_character(
                123, 22, 2, now=1000
            ),
            []
        )

        with self.assertRaisesRegex(ValueError, 'çµæ'):
            live_social.respond(
                123, 1, 22, 2, True, now=1000
            )

        with self.assertRaisesRegex(ValueError, 'çµæ'):
            live_social.invite(
                123, sid, 11, 3, now=1000
            )

    def test_sales_offer_rejected_after_expiry(self):
        sid = live_stream.start(
            123, 11, 1, 'sell',
            'sales', 'fixed', 15,
            now=100
        )

        with self.assertRaises(ValueError):
            live_commerce.propose_offer(
                123, 11, sid,
                'sku1', 'bag', 100, 1,
                now=1000
            )

        with database.get_connection() as db:
            self.assertEqual(
                db.execute(
                    'SELECT COUNT(*) FROM rp_live_sale_offers'
                ).fetchone()[0],
                0
            )

    def test_pk_challenge_rejected_when_either_stream_expired(self):
        a = live_stream.start(
            123, 11, 1, 'a',
            'pk', 'fixed', 15,
            now=100
        )

        b = live_stream.start(
            123, 22, 2, 'b',
            'pk', 'realtime',
            now=100
        )

        with self.assertRaises(ValueError):
            live_pk.challenge(
                123, 11, a, b, now=1000
            )

        with database.get_connection() as db:
            self.assertEqual(
                db.execute(
                    'SELECT COUNT(*) FROM rp_live_pk_matches'
                ).fetchone()[0],
                0
            )


    def test_pk_invite_not_visible_if_challenger_stops(self):
        host = live_stream.start(123, 11, 1, 'host', 'pk', 'realtime', now=100)
        guest = live_stream.start(123, 22, 2, 'guest', 'pk', 'realtime', now=100)
        live_pk.challenge(123, 11, host, guest, now=101)
        self.assertEqual(len(live_pk.pending(123, 22, 2, now=101)), 1)
        live_stream.stop(123, 11, host, now=102)
        self.assertEqual(live_pk.pending(123, 22, 2, now=103), [])


if __name__ == '__main__':
    unittest.main()
