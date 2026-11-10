
"""Day-only notices: bounds, 30-day budget, idempotent sources, completion, no production DB."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from core import database
from core import work_duration as w
from modules.task_repository import initialize, accept_task


class WorkDurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(
            database,
            "DATABASE_PATH",
            str(Path(self.tmp.name) / "days.sqlite"),
        )
        self.dbpatch.start()
        database.init_db()
        initialize()

        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id) VALUES ('a')")
            db.execute(
                "INSERT INTO characters(guild_id,owner_id,name,status) "
                "VALUES ('a','owner','R','active')"
            )
            self.cid = db.execute(
                "SELECT id FROM characters WHERE guild_id='a'"
            ).fetchone()[0]
            db.execute(
                """INSERT INTO rp_task_definitions
                (guild_id,code,kind,title,description,subtype,level,
                 requirements_json,rewards_json,report_json,status)
                VALUES
                ('a','t1','screen','電影','','電影',1,
                 '{}','{}','{}','published')"""
            )
            self.tid = db.execute(
                "SELECT id FROM rp_task_definitions WHERE guild_id='a'"
            ).fetchone()[0]

    def tearDown(self):
        self.dbpatch.stop()
        self.tmp.cleanup()

    def test_nine_category_bounds(self):
        for kind, (start, end) in w.BOUNDS.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    w.validate_duration(kind, start),
                    (kind, start),
                )
                self.assertEqual(
                    w.validate_duration(kind, end),
                    (kind, end),
                )
                for invalid in (start - 1, end + 1):
                    with self.assertRaises(ValueError):
                        w.validate_duration(kind, invalid)

        with self.assertRaises(ValueError):
            w.validate_duration("電影", "8.5")

    def test_chinese_counts_no_punctuation(self):
        self.assertEqual(
            w.count_characters("你好，世界！ ABC 123"),
            10,
        )

    def _accept(self, days):
        w.set_duration("a", self.tid, "電影", days, "admin")
        self.assertTrue(accept_task("a", self.cid, self.tid))
        self.assertEqual(
            w.assignment("a", self.cid, self.tid)["required_days"],
            days,
        )

    def test_legacy_no_duration_unaffected(self):
        self.assertTrue(accept_task("a", self.cid, self.tid))
        self.assertIsNone(w.assignment("a", self.cid, self.tid))

    def test_auto_completion_once_no_double_credits(self):
        self._accept(180)
        now = datetime.now(timezone.utc)

        result = w.submit_source(
            "a", self.cid, self.tid,
            "msg:100", "甲" * 1800, at=now,
        )
        self.assertEqual(
            (result["completed_days"], result["remaining_days"]),
            (180, 0),
        )
        self.assertTrue(result["newly_completed"])

        repeated = w.submit_source(
            "a", self.cid, self.tid,
            "msg:100", "甲" * 1800, at=now,
        )
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["credited_days"], 0)

        self.assertEqual(
            w.budget("a", self.cid, at=now)["remaining_days"],
            185,
        )

        with self.assertRaises(ValueError):
            w.submit_source(
                "a", self.cid, self.tid,
                "msg:new", "乙" * 10, at=now,
            )

    def test_30_day_reset_caps_at_365(self):
        self._accept(730)
        now = datetime.now(timezone.utc)

        result = w.submit_source(
            "a", self.cid, self.tid,
            "msg:200", "甲" * 7300, at=now,
        )
        self.assertEqual(result["completed_days"], 365)
        self.assertEqual(result["remaining_days"], 365)
        self.assertEqual(result["budget_remaining"], 0)

        result = w.submit_source(
            "a", self.cid, self.tid,
            "msg:200", "甲" * 7300,
            at=now + timedelta(days=30, seconds=1),
        )
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["completed_days"], 730)
        self.assertTrue(result["newly_completed"])
        self.assertEqual(result["budget_remaining"], 0)

    def test_same_text_different_message_cannot_double_count(self):
        self._accept(180)

        first = w.submit_source(
            "a", self.cid, self.tid,
            "msg:dup1", "新的測試內容" * 20,
        )
        with self.assertRaises(ValueError):
            w.submit_source(
                "a", self.cid, self.tid,
                "msg:dup2", "新的測試內容" * 20,
            )
        self.assertEqual(
            w.assignment("a", self.cid, self.tid)["completed_days"],
            first["completed_days"],
        )

    def test_budget_preview_does_not_write(self):
        self.assertEqual(w.budget_preview("a", self.cid), 365)
        with database.get_connection() as db:
            self.assertFalse(
                db.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE name='rp_work_duration_budget'"
                ).fetchone()
            )

    def test_web_drama_alias(self):
        self.assertEqual(
            w.validate_duration("網路劇", 40),
            ("網劇", 40),
        )

    def test_source_reuse_denied(self):
        self._accept(180)
        w.submit_source(
            "a", self.cid, self.tid,
            "msg:300", "甲" * 10,
        )
        with self.assertRaises(ValueError):
            w.submit_source(
                "a", self.cid, self.tid + 1,
                "msg:300", "甲" * 10,
            )

    def test_frozen_days_on_existing_acceptance(self):
        self._accept(180)
        w.set_duration("a", self.tid, "電影", 200, "admin")
        self.assertEqual(
            w.assignment("a", self.cid, self.tid)["required_days"],
            180,
        )


if __name__ == "__main__":
    unittest.main()
