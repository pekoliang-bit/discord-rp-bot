
"""付費公關測試：使用真實 core 模組，避免污染其他測試的匯入快取。"""

import sqlite3
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch
from core import rumor_pr_paid_flow as m


class Test(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "x.db"

        with sqlite3.connect(self.p) as db:
            db.executescript("""
                CREATE TABLE characters(
                    guild_id TEXT,
                    id INTEGER,
                    owner_id TEXT
                );

                INSERT INTO characters
                VALUES("g", 1, "u");
            """)
            m.initialize_pr_strategy_config(db)

    def con(self):
        db = sqlite3.connect(self.p)
        db.row_factory = sqlite3.Row
        return db

    def test_schema_no_default_score(self):
        with self.con() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) "
                    "FROM rp_rumor_pr_strategy_config"
                ).fetchone()[0],
                0,
            )

    def test_disabled_no_charge(self):
        with (
            patch.object(
                m,
                "get_connection",
                side_effect=self.con,
            ),
            patch.object(
                m,
                "charge_verified_pr_action",
            ) as charge,
        ):
            with self.assertRaises(LookupError):
                m.perform_paid_pr_action(
                    "g",
                    "u",
                    1,
                    2,
                    "公開澄清",
                    "coins",
                    "k",
                    trusted=True,
                )

            charge.assert_not_called()

    def test_retry_after_config_removed(self):
        with self.con() as db:
            db.executescript("""
                CREATE TABLE rp_rumor_pr_charges(
                    id INTEGER PRIMARY KEY,
                    guild_id TEXT,
                    request_key TEXT,
                    rumor_id INTEGER,
                    character_id INTEGER,
                    strategy TEXT,
                    resource TEXT,
                    cost INTEGER,
                    repeat_count INTEGER,
                    effectiveness REAL
                );

                CREATE TABLE rp_rumor_pr_effects(
                    guild_id TEXT,
                    action_id INTEGER,
                    rumor_id INTEGER,
                    character_id INTEGER,
                    strategy TEXT,
                    base_score INTEGER,
                    multiplier TEXT,
                    effective_score INTEGER
                );
            """)

            db.execute(
                """
                INSERT INTO rp_rumor_pr_charges
                VALUES(
                    10, "g", "k", 2, 1,
                    "公開澄清", "coins",
                    1000, 1, 1.0
                )
                """
            )

            db.execute(
                """
                INSERT INTO rp_rumor_pr_effects
                VALUES(
                    "g", 10, 2, 1,
                    "公開澄清", 30, "1", 30
                )
                """
            )

        with (
            patch.object(
                m,
                "get_connection",
                side_effect=self.con,
            ),
            patch.object(
                m,
                "charge_verified_pr_action",
            ) as charge,
        ):
            r = m.perform_paid_pr_action(
                "g",
                "u",
                1,
                2,
                "公開澄清",
                "coins",
                "k",
                trusted=True,
            )

            self.assertTrue(r.duplicate)
            self.assertEqual(
                r.effective_score,
                30,
            )
            charge.assert_not_called()

    def test_retry_wrong_actor(self):
        self.test_retry_after_config_removed()

        with patch.object(
            m,
            "get_connection",
            side_effect=self.con,
        ):
            with self.assertRaises(PermissionError):
                m.perform_paid_pr_action(
                    "g",
                    "intruder",
                    1,
                    2,
                    "公開澄清",
                    "coins",
                    "k",
                    trusted=True,
                )


if __name__ == "__main__":
    unittest.main()
