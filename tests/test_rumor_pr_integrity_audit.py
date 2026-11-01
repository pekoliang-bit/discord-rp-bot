
"""真實暫存 SQLite 公關稽核測試；不污染全域核心模組。"""

import sqlite3
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from core import rumor_pr_integrity_audit as mod


class Connection:

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        return self.db

    def __exit__(self, *_):
        self.db.close()


class IntegrityTests(unittest.TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        self.path = str(
            Path(folder.name) / "pr.sqlite"
        )

        with Connection(self.path) as db:
            db.executescript("""
                CREATE TABLE characters(
                    guild_id TEXT,
                    id INTEGER,
                    owner_id TEXT
                );

                CREATE TABLE rp_company_rumors(
                    guild_id TEXT,
                    id INTEGER,
                    character_id INTEGER
                );

                CREATE TABLE rp_rumor_pr_charges(
                    guild_id TEXT,
                    rumor_id INTEGER,
                    character_id INTEGER,
                    id INTEGER,
                    strategy TEXT,
                    resource TEXT,
                    cost INTEGER,
                    repeat_count INTEGER,
                    effectiveness TEXT
                );

                CREATE TABLE rp_rumor_pr_effects(
                    guild_id TEXT,
                    rumor_id INTEGER,
                    character_id INTEGER,
                    action_id INTEGER,
                    strategy TEXT,
                    base_score INTEGER,
                    multiplier TEXT,
                    effective_score INTEGER
                );

                INSERT INTO rp_company_rumors
                VALUES('g', 9, 1);

                INSERT INTO rp_rumor_pr_charges
                VALUES(
                    'g', 9, 1, 4,
                    '澄清', 'coins',
                    1000, 1, '1'
                );

                INSERT INTO rp_rumor_pr_effects
                VALUES(
                    'g', 9, 1, 4,
                    '澄清', 100, '1', 100
                );
            """)

            db.commit()

        p = patch.object(
            mod,
            "get_connection",
            lambda: Connection(self.path),
        )

        p.start()
        self.addCleanup(p.stop)

    def audit(self):
        return mod.audit_pr_case(
            "g",
            9,
            1,
            trusted=True,
        )

    def test_healthy(self):
        result = self.audit()

        self.assertTrue(result.healthy)

        self.assertEqual(
            (
                result.charge_count,
                result.total_score,
                result.coins_spent,
            ),
            (1, 100, 1000),
        )

    def test_no_permission(self):
        with self.assertRaises(PermissionError):
            mod.audit_pr_case(
                "g",
                9,
                1,
            )

    def test_missing_effect(self):
        with Connection(self.path) as db:
            db.execute(
                "DELETE FROM rp_rumor_pr_effects"
            )
            db.commit()

        self.assertIn(
            "缺少效果：#4",
            self.audit().issues,
        )

    def test_orphan_effect(self):
        with Connection(self.path) as db:
            db.execute(
                """
                INSERT INTO rp_rumor_pr_effects
                VALUES(
                    'g', 9, 1, 8,
                    '澄清', 100, '1', 100
                )
                """
            )
            db.commit()

        self.assertIn(
            "孤兒效果：#8",
            self.audit().issues,
        )

    def test_wrong_score(self):
        with Connection(self.path) as db:
            db.execute(
                """
                UPDATE rp_rumor_pr_effects
                SET effective_score=999
                """
            )
            db.commit()

        self.assertIn(
            "效果計算不一致：#4",
            self.audit().issues,
        )

    def test_other_case(self):
        with self.assertRaises(LookupError):
            mod.audit_pr_case(
                "g",
                9,
                2,
                trusted=True,
            )


if __name__ == "__main__":
    unittest.main()
