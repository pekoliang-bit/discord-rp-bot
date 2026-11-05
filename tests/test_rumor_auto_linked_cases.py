
"""雙角色案件、發布時間及重試去重測試。

使用獨立 SQLite，不對外發文。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import database
from core.database import (
    init_db,
    transaction,
    get_connection,
)
from core import rumor_auto_broadcast as auto
from core import rumor_auto_case_bridge as bridge


class LinkedCaseTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = database.DATABASE_PATH

        database.DATABASE_PATH = (
            Path(self.tmp.name) / "rumor.sqlite"
        )

        init_db()
        auto.initialize()
        bridge.initialize_links()

        with transaction() as db:
            db.execute("""
                INSERT INTO guilds(guild_id, name)
                VALUES ('900', 'test')
            """)

            for name in ("甲", "乙"):
                db.execute("""
                    INSERT INTO characters(
                        guild_id,
                        owner_id,
                        name,
                        status
                    )
                    VALUES ('900', 'u', ?, 'active')
                """, (name,))

            db.execute("""
                CREATE TABLE IF NOT EXISTS
                rp_company_rumors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    character_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    settle_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                )
            """)

        self.published = int(time.time()) - 7200

        # 建立模擬 Discord 雪花 ID。
        self.snowflake = str(
            (
                (
                    self.published * 1000
                    - 1420070400000
                ) << 22
            ) + 1
        )

        self.payload = {
            "guild_id": "900",
            "channel_id": "1000",
            "category": "工作",
            "character_ids": [1, 2],
            "text": "甲和乙",
            "template_id": "P001",
        }

        with transaction() as db:
            db.execute("""
                INSERT INTO rp_rumor_auto_outbox (
                    id,
                    guild_id,
                    channel_id,
                    scheduled_at,
                    payload_json,
                    state,
                    message_id,
                    retry_at,
                    created_at
                )
                VALUES (
                    'job',
                    '900',
                    '1000',
                    ?,
                    ?,
                    'done',
                    ?,
                    0,
                    ?
                )
            """, (
                self.published - 50,
                json.dumps(self.payload),
                self.snowflake,
                self.published - 50,
            ))

        # 測試案件連動時，隔離外部正式規則服務。
        self.patches = [
            patch.object(
                bridge,
                "init_rumors",
                lambda: None,
            ),
            patch.object(
                bridge,
                "init_snapshots",
                lambda: None,
            ),
            patch.object(
                bridge,
                "get_active_rules",
                lambda gid: object(),
            ),
            patch.object(
                bridge,
                "resolve_tier_in_transaction",
                lambda *args: 1,
            ),
            patch.object(
                bridge,
                "resolve_complexity_in_transaction",
                lambda *args: SimpleNamespace(
                    complexity=1,
                    version="v1",
                ),
            ),
            patch.object(
                bridge,
                "rumor_allowed",
                lambda *args: True,
            ),
            patch.object(
                bridge,
                "insert_case_and_snapshot",
                self.insert_case,
            ),
        ]

        for patcher in self.patches:
            patcher.start()

    @staticmethod
    def insert_case(db, **kwargs):
        cur = db.execute("""
            INSERT INTO rp_company_rumors (
                guild_id,
                character_id,
                created_at,
                settle_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            kwargs["guild_id"],
            kwargs["character_id"],
            kwargs["published_at"],
            (
                kwargs["published_at"]
                + kwargs["settle_seconds"]
            ),
        ))

        return cur.lastrowid

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

        database.DATABASE_PATH = self.old_db
        self.tmp.cleanup()

    def test_both_participants_and_original_deadline(self):
        primary, reason = bridge.create_linked_case(
            "job"
        )

        self.assertIsNone(reason)

        cases = bridge.linked_cases("job")

        self.assertEqual(len(cases), 2)
        self.assertEqual(
            primary,
            cases[0]["rumor_id"],
        )

        with get_connection() as db:
            rows = db.execute("""
                SELECT
                    character_id,
                    created_at,
                    settle_at
                FROM rp_company_rumors
                ORDER BY id
            """).fetchall()

        self.assertEqual(
            [row["character_id"] for row in rows],
            [1, 2],
        )

        self.assertTrue(
            all(
                row["created_at"] == self.published
                for row in rows
            )
        )

        self.assertTrue(
            all(
                row["settle_at"]
                == self.published + 72 * 3600
                for row in rows
            )
        )

    def test_retry_does_not_duplicate(self):
        bridge.create_linked_case("job")
        bridge.create_linked_case("job")

        self.assertEqual(
            len(bridge.linked_cases("job")),
            2,
        )

        with get_connection() as db:
            count = db.execute("""
                SELECT COUNT(*)
                FROM rp_company_rumors
            """).fetchone()[0]

        self.assertEqual(count, 2)

    def test_reconcile_includes_missing_second_participant(self):
        bridge.create_linked_case("job")

        with transaction() as db:
            db.execute("""
                DELETE FROM rp_rumor_autopost_case_links
                WHERE character_id=2
            """)

            db.execute("""
                DELETE FROM rp_company_rumors
                WHERE character_id=2
            """)

        result = bridge.reconcile_unlinked()

        self.assertEqual(len(result), 1)

        self.assertEqual(
            len(bridge.linked_cases("job")),
            2,
        )


if __name__ == "__main__":
    unittest.main()
