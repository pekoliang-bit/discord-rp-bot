
"""正式結案器權限、編號與早於結算時間的阻擋測試。"""

import sqlite3
import unittest

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
            VALUES ('g', 7, 3, 'active', 9999999999)
            """
        )

    def __enter__(self):
        return self.db

    def __exit__(self, *args):
        self.db.close()


class Guards(unittest.TestCase):

    def test_untrusted(self):
        with self.assertRaises(PermissionError):
            mod.finalize_locked_case(
                "g",
                7,
            )

    def test_invalid_id(self):
        with self.assertRaises(ValueError):
            mod.finalize_locked_case(
                "g",
                0,
                trusted=True,
            )

    def test_before_due(self):
        with patch.object(
            mod,
            "get_connection",
            side_effect=MemoryConnection,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "尚未到達",
            ):
                mod.finalize_locked_case(
                    "g",
                    7,
                    trusted=True,
                    now=100,
                )


if __name__ == "__main__":
    unittest.main()
