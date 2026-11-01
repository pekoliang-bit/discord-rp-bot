
"""缺少正式評級或方案時，結案器不得呼叫獎懲核心。"""

import sqlite3
import unittest

from types import SimpleNamespace as NS
from unittest.mock import patch

from core import rumor_finalization as mod


class MemoryConnection:

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row

        self.db.execute(
            """
            CREATE TABLE rp_company_rumors (
                guild_id TEXT,
                id INTEGER,
                character_id INTEGER,
                status TEXT,
                settle_at INTEGER
            )
            """
        )

        self.db.execute(
            """
            INSERT INTO rp_company_rumors
            VALUES ('g', 7, 3, 'active', 10)
            """
        )

    def __enter__(self):
        return self.db

    def __exit__(self, *args):
        self.db.close()


class NoPrematurePayout(unittest.TestCase):

    def test_missing_plan_blocks_payout(self):
        with (
            patch.object(
                mod,
                "get_connection",
                side_effect=MemoryConnection,
            ),
            patch.object(
                mod,
                "load_reward_plan",
                side_effect=LookupError("無鎖定方案"),
            ),
            patch.object(
                mod,
                "settle_character_reward",
            ) as payout,
        ):
            with self.assertRaises(LookupError):
                mod.finalize_locked_case(
                    "g",
                    7,
                    trusted=True,
                    now=20,
                )

            payout.assert_not_called()

    def test_missing_grade_blocks_payout(self):
        plan = NS(
            grade="A",
            tier=2,
            heat=100,
            version="v1",
            changes={"acting": 1},
        )

        with (
            patch.object(
                mod,
                "get_connection",
                side_effect=MemoryConnection,
            ),
            patch.object(
                mod,
                "load_reward_plan",
                return_value=plan,
            ),
            patch.object(
                mod,
                "load_grade",
                side_effect=LookupError("無評級"),
            ),
            patch.object(
                mod,
                "settle_character_reward",
            ) as payout,
        ):
            with self.assertRaises(LookupError):
                mod.finalize_locked_case(
                    "g",
                    7,
                    trusted=True,
                    now=20,
                )

            payout.assert_not_called()

    def test_grade_mismatch_blocks_payout(self):
        plan = NS(
            grade="A",
            tier=2,
            heat=100,
            version="v1",
            changes={"acting": 1},
        )

        snapshot = NS(
            grade="B",
            tier=2,
            heat=100,
            rule_version="v1",
        )

        with (
            patch.object(
                mod,
                "get_connection",
                side_effect=MemoryConnection,
            ),
            patch.object(
                mod,
                "load_reward_plan",
                return_value=plan,
            ),
            patch.object(
                mod,
                "load_grade",
                return_value=snapshot,
            ),
            patch.object(
                mod,
                "settle_character_reward",
            ) as payout,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "不一致",
            ):
                mod.finalize_locked_case(
                    "g",
                    7,
                    trusted=True,
                    now=20,
                )

            payout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
