
"""SQLite 同時結算及重試整合測試；只使用暫存資料庫。

安裝：tests/test_concurrent_rumor_reward.py
執行：
python -m unittest discover -s tests \
    -p 'test_concurrent_rumor_reward.py' -v
"""
import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import config, database, stat_rewards
from core import rumor_plan_store
from modules import rumor_settlement


class ConcurrentRumorRewardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(
            prefix="rp_concurrent_"
        )
        self.addCleanup(tmp.cleanup)

        db_path = Path(tmp.name) / "test.sqlite3"

        for module, key in (
            (config, "DATABASE_PATH"),
            (database, "DATABASE_PATH"),
        ):
            guard = patch.object(
                module, key, str(db_path)
            )
            guard.start()
            self.addCleanup(guard.stop)

        with database.transaction() as db:
            db.execute(
                "CREATE TABLE guilds("
                "guild_id TEXT PRIMARY KEY)"
            )
            db.execute(
                "CREATE TABLE characters("
                "id INTEGER PRIMARY KEY,"
                "guild_id TEXT,status TEXT,"
                "UNIQUE(guild_id,id))"
            )
            db.execute(
                "CREATE TABLE attributes("
                "guild_id TEXT,character_id INTEGER,"
                "name TEXT,value INTEGER NOT NULL,"
                "version INTEGER DEFAULT 0,"
                "PRIMARY KEY(guild_id,character_id,name))"
            )
            db.execute(
                "CREATE TABLE rp_company_rumors("
                "id INTEGER PRIMARY KEY,"
                "guild_id TEXT,character_id INTEGER,"
                "status TEXT)"
            )
            db.execute(
                "INSERT INTO guilds VALUES('g')"
            )
            db.execute(
                "INSERT INTO characters "
                "VALUES(1,'g','active')"
            )
            db.execute(
                "INSERT INTO attributes "
                "VALUES('g',1,'路人緣',10,0)"
            )
            db.execute(
                "INSERT INTO rp_company_rumors "
                "VALUES(9,'g',1,'active')"
            )

        stat_rewards.init_db()
        rumor_settlement.initialize()

        guard = patch.object(
            rumor_plan_store,
            "load_reward_plan",
            return_value=SimpleNamespace(
                grade="A",
                changes={"路人緣": 5},
            ),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def run_once(self):
        return rumor_settlement.settle_character_reward(
            "g",
            9,
            1,
            "A",
            {"路人緣": 5},
            trusted=True,
        )

    def values(self):
        with database.get_connection() as db:
            value = db.execute(
                "SELECT value FROM attributes "
                "WHERE guild_id='g' "
                "AND character_id=1 "
                "AND name='路人緣'"
            ).fetchone()[0]

            rows = db.execute(
                "SELECT state "
                "FROM rp_rumor_reward_jobs"
            ).fetchall()

            logs = db.execute(
                "SELECT count(*) "
                "FROM rp_stat_reward_log "
                "WHERE source='rumor'"
            ).fetchone()[0]

        return value, [r[0] for r in rows], logs

    def test_parallel_same_case_once(self):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=8
        ) as pool:
            results = list(
                pool.map(
                    lambda _: self.run_once(),
                    range(16),
                )
            )

        self.assertEqual(
            self.values(),
            (15, ["applied"], 1),
        )
        self.assertEqual(len(results), 16)

    def test_relaunch_retry(self):
        self.run_once()
        self.assertTrue(self.run_once().duplicate)
        self.assertEqual(
            self.values(),
            (15, ["applied"], 1),
        )

    def test_mismatched_payload_rejected(self):
        self.run_once()

        with self.assertRaises(ValueError):
            rumor_settlement.settle_character_reward(
                "g",
                9,
                1,
                "B",
                {"路人緣": 5},
                trusted=True,
            )

        self.assertEqual(
            self.values(),
            (15, ["applied"], 1),
        )


if __name__ == "__main__":
    unittest.main()
