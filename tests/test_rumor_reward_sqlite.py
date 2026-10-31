
"""真實 SQLite 傳聞數值獎懲重試測試。

放置：tests/test_rumor_reward_sqlite.py
執行：
python -m unittest discover -s tests \
    -p test_rumor_reward_sqlite.py -v

使用獨立暫存 SQLite，不修改正式玩家資料。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import database, stat_rewards, rumor_plan_store
from modules import rumor_rewards, rumor_settlement


class TestRumorRewardSQLite(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="rumor_test_"
        )
        self.addCleanup(self.temp.cleanup)

        path = str(
            Path(self.temp.name) / "test.sqlite3"
        )

        db_patch = patch.object(
            database, "DATABASE_PATH", path
        )
        db_patch.start()
        self.addCleanup(db_patch.stop)

        with database.transaction() as db:
            db.execute("""
                CREATE TABLE guilds (
                    guild_id TEXT PRIMARY KEY
                )
            """)

            db.execute("""
                CREATE TABLE characters (
                    id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE(guild_id, id)
                )
            """)

            db.execute("""
                CREATE TABLE attributes (
                    guild_id TEXT,
                    character_id INTEGER,
                    name TEXT,
                    value INTEGER NOT NULL DEFAULT 0
                        CHECK(value >= 0),
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(
                        guild_id, character_id, name
                    )
                )
            """)

            db.execute("""
                CREATE TABLE rp_company_rumors (
                    id INTEGER PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    character_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                )
            """)

            db.execute(
                "INSERT INTO guilds VALUES ('g')"
            )
            db.execute(
                "INSERT INTO characters "
                "VALUES (1,'g','active')"
            )
            db.execute(
                "INSERT INTO rp_company_rumors "
                "VALUES (9,'g',1,'active')"
            )
            db.execute(
                "INSERT INTO attributes "
                "VALUES ('g',1,'路人緣',10,0)"
            )

        stat_rewards.init_db()
        rumor_settlement.initialize()

        # 模擬已核定的正式獎懲方案。
        plan = type(
            "Plan", (), {
                "grade": "A",
                "changes": {"路人緣": 5},
            }
        )()

        plan_patch = patch.object(
            rumor_plan_store,
            "load_reward_plan",
            return_value=plan,
        )
        plan_patch.start()
        self.addCleanup(plan_patch.stop)

    def value(self):
        with database.get_connection() as db:
            row = db.execute(
                "SELECT value FROM attributes "
                "WHERE guild_id='g' "
                "AND character_id=1 "
                "AND name='路人緣'"
            ).fetchone()
            return int(row[0])

    def job_state(self):
        with database.get_connection() as db:
            row = db.execute(
                "SELECT state "
                "FROM rp_rumor_reward_jobs "
                "WHERE guild_id='g' "
                "AND rumor_id=9 "
                "AND phase='final' "
                "AND character_id=1"
            ).fetchone()
            return row[0] if row else None

    def settle(self):
        return rumor_settlement.settle_character_reward(
            "g",
            9,
            1,
            "A",
            {"路人緣": 5},
            trusted=True,
        )

    def test_once_and_retry(self):
        first = self.settle()
        self.assertFalse(first.duplicate)
        self.assertEqual(self.value(), 15)
        self.assertEqual(
            self.job_state(), "applied"
        )

        second = self.settle()
        self.assertTrue(second.duplicate)
        self.assertEqual(self.value(), 15)

    def test_crash_after_grant_before_mark(self):
        original = rumor_settlement.apply_settlement

        def crash_once(preview, *, authorized):
            original(
                preview,
                authorized=authorized,
            )
            raise RuntimeError(
                "模擬發獎後崩潰"
            )

        with patch.object(
            rumor_settlement,
            "apply_settlement",
            side_effect=crash_once,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "模擬發獎後崩潰"
            ):
                self.settle()

        self.assertEqual(self.value(), 15)
        self.assertEqual(
            self.job_state(), "prepared"
        )

        self.settle()

        self.assertEqual(self.value(), 15)
        self.assertEqual(
            self.job_state(), "applied"
        )

    def test_changed_payload_rejected(self):
        self.settle()

        with self.assertRaises(ValueError):
            rumor_settlement.settle_character_reward(
                "g",
                9,
                1,
                "A",
                {"路人緣": 7},
                trusted=True,
            )

        self.assertEqual(self.value(), 15)

    def test_unauthorized_rejected(self):
        with self.assertRaises(PermissionError):
            rumor_settlement.settle_character_reward(
                "g",
                9,
                1,
                "A",
                {"路人緣": 5},
            )

        self.assertEqual(self.value(), 10)

    def test_stat_source_idempotent(self):
        kwargs = dict(
            source="rumor",
            source_id="manual:1",
            reason="test",
            random=False,
            purchased=False,
        )

        first = stat_rewards.grant_stats(
            "g", 1, {"路人緣": 5}, **kwargs
        )
        second = stat_rewards.grant_stats(
            "g", 1, {"路人緣": 5}, **kwargs
        )

        self.assertTrue(first["applied"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.value(), 15)


if __name__ == "__main__":
    unittest.main()
