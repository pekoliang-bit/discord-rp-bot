
"""離線驗證：定時刊登排程、去重、授權與工作單續跑。不連接 Discord。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core import database
from core.database import (
    init_db,
    transaction,
)
from core import rumor_auto_broadcast as auto


class AutoRumorDeliveryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = database.DATABASE_PATH

        database.DATABASE_PATH = (
            Path(self.tmp.name) / "test.sqlite"
        )

        init_db()
        auto.initialize()

        with transaction() as db:
            db.execute(
                """
                INSERT INTO guilds(guild_id, name)
                VALUES ('900', 'test')
                """
            )

            db.execute(
                """
                INSERT INTO characters(
                    guild_id,
                    owner_id,
                    name,
                    status,
                    character_type
                )
                VALUES (
                    '900',
                    'user1',
                    '甲',
                    'active',
                    'player'
                )
                """
            )

            db.execute(
                """
                INSERT INTO characters(
                    guild_id,
                    owner_id,
                    name,
                    status,
                    character_type
                )
                VALUES (
                    '900',
                    'user2',
                    '乙',
                    'active',
                    'player'
                )
                """
            )

        auto.set_channel(
            "900",
            "1000",
            "owner",
        )

        auto.set_enabled(
            "900",
            True,
            "owner",
        )

    def tearDown(self):
        database.DATABASE_PATH = self.old_path
        self.tmp.cleanup()

    def due(self):
        with transaction() as db:
            db.execute(
                """
                UPDATE rp_rumor_autopost_settings
                SET next_due=?
                WHERE guild_id='900'
                """,
                (int(time.time()) - 1,),
            )

    def test_claim_retry_and_success(self):
        self.due()

        self.assertIsNotNone(
            auto.claim("900")
        )

        self.assertIsNone(
            auto.claim("900")
        )

        jobs = auto.pending_jobs()

        self.assertEqual(
            len(jobs),
            1,
        )

        job_id = jobs[0]
        leased = auto.lease_job(job_id)

        self.assertTrue(
            auto.still_consented(
                "900",
                leased["entry"],
            )
        )

        auto.mark_retry(
            job_id,
            "temporary error",
        )

        self.assertEqual(
            auto.recent_job_status("900")[0]["attempts"],
            1,
        )

        auto.mark_sent(
            job_id,
            "12345678",
        )

        auto.finish_job(job_id)
        auto.finish_job(job_id)

        self.assertEqual(
            auto.recent_job_status("900")[0]["state"],
            "done",
        )

        self.assertEqual(
            len(auto.recent_posts("900")),
            1,
        )

    def test_disabled_and_optout(self):
        self.due()

        entry = auto.claim("900")

        self.assertIsNotNone(entry)

        with transaction() as db:
            for cid in entry["character_ids"]:
                db.execute(
                    """
                    INSERT INTO rp_rumor_optout(
                        guild_id,
                        character_id,
                        enabled,
                        updated_at
                    )
                    VALUES ('900', ?, 0, 0)
                    """,
                    (cid,),
                )

        self.assertFalse(
            auto.still_consented(
                "900",
                entry,
            )
        )

        auto.set_enabled(
            "900",
            False,
            "owner",
        )

        self.assertEqual(
            auto.due_guild_ids(),
            [],
        )

    def test_unconfigured_guild_is_off(self):
        self.assertFalse(
            auto.settings("9999")["enabled"]
        )

        self.assertIsNone(
            auto.settings("9999")["channel_id"]
        )


if __name__ == "__main__":
    unittest.main()
