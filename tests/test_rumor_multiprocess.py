
"""獨立多進程 SQLite 傳聞發獎測試。

安裝：tests/test_rumor_multiprocess.py
執行：
python -m unittest discover -s tests -p test_rumor_multiprocess.py -v

只使用暫存資料庫，不操作正式玩家資料。
"""
from __future__ import annotations

import concurrent.futures
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _worker(database_path: str) -> tuple[str, bool]:
    """每個程序使用自己的模組及 SQLite 連線。"""

    from core import database, rumor_plan_store
    from modules import rumor_settlement

    with (
        patch.object(
            database, "DATABASE_PATH", database_path
        ),
        patch.object(
            rumor_plan_store,
            "load_reward_plan",
            return_value=SimpleNamespace(
                grade="A",
                changes={"路人緣": 5},
            ),
        ),
    ):
        result = rumor_settlement.settle_character_reward(
            "g",
            9,
            1,
            "A",
            {"路人緣": 5},
            trusted=True,
        )
        return result.state, result.duplicate


class MultiprocessRumorRewardTests(unittest.TestCase):

    def setUp(self):
        from core import database, stat_rewards
        from modules import rumor_settlement

        tmp = tempfile.TemporaryDirectory(
            prefix="rp_multiprocess_"
        )
        self.addCleanup(tmp.cleanup)

        self.database_path = str(
            Path(tmp.name) / "sandbox.sqlite3"
        )

        guard = patch.object(
            database,
            "DATABASE_PATH",
            self.database_path,
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
                "guild_id TEXT,"
                "character_id INTEGER,"
                "name TEXT,"
                "value INTEGER NOT NULL,"
                "version INTEGER DEFAULT 0,"
                "PRIMARY KEY("
                "guild_id,character_id,name))"
            )
            db.execute(
                "CREATE TABLE rp_company_rumors("
                "id INTEGER PRIMARY KEY,"
                "guild_id TEXT,"
                "character_id INTEGER,"
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

    def _snapshot(self):
        from core import database

        with database.get_connection() as db:
            score = db.execute(
                "SELECT value FROM attributes "
                "WHERE guild_id='g' "
                "AND character_id=1 "
                "AND name='路人緣'"
            ).fetchone()[0]

            jobs = db.execute(
                "SELECT state "
                "FROM rp_rumor_reward_jobs "
                "WHERE rumor_id=9"
            ).fetchall()

            history = db.execute(
                "SELECT COUNT(*) "
                "FROM rp_stat_reward_log "
                "WHERE source='rumor'"
            ).fetchone()[0]

        return score, [r[0] for r in jobs], history

    def test_spawned_processes_only_reward_once(self):
        context = multiprocessing.get_context("spawn")

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=4,
            mp_context=context,
        ) as pool:
            results = list(
                pool.map(
                    _worker,
                    [self.database_path] * 12,
                )
            )

        self.assertEqual(len(results), 12)
        self.assertTrue(
            all(
                state == "applied"
                for state, _ in results
            )
        )
        self.assertEqual(
            self._snapshot(),
            (15, ["applied"], 1),
        )

    def test_retry_from_new_process(self):
        context = multiprocessing.get_context("spawn")

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=2,
            mp_context=context,
        ) as pool:
            list(
                pool.map(
                    _worker,
                    [self.database_path] * 2,
                )
            )

        self.assertEqual(
            self._snapshot(),
            (15, ["applied"], 1),
        )

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=1,
            mp_context=context,
        ) as pool:
            state, duplicate = next(
                pool.map(
                    _worker,
                    [self.database_path],
                )
            )

        self.assertEqual(state, "applied")
        self.assertTrue(duplicate)
        self.assertEqual(
            self._snapshot(),
            (15, ["applied"], 1),
        )


if __name__ == "__main__":
    unittest.main()
