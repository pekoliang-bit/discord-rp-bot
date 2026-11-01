
"""傳聞到期預覽測試；使用實際模組與區域性 mock。"""

import unittest

from types import SimpleNamespace as NS
from unittest.mock import patch
from core import rumor_due_preview as mod


class PreviewTests(unittest.TestCase):

    def test_permission(self):
        with self.assertRaises(PermissionError):
            mod.preview_due_cases("1")

    def test_success_and_skip(self):
        cases = [
            NS(
                guild_id="1",
                rumor_id=2,
                character_id=3,
                state="needs_grade",
                explanation="",
            ),
            NS(
                guild_id="1",
                rumor_id=4,
                character_id=5,
                state="needs_plan",
                explanation="方案缺失",
            ),
        ]

        with (
            patch.object(
                mod,
                "inspect_due_cases",
                return_value=cases,
            ),
            patch.object(
                mod,
                "lock_prepared_grade",
                return_value=NS(
                    dry_run=True,
                    character_id=3,
                    grade="A",
                    score=80,
                ),
            ) as lock,
        ):
            out = mod.preview_due_cases(
                "1",
                trusted=True,
            )

            self.assertEqual(
                [x.result for x in out],
                [
                    "ready_for_review",
                    "skipped",
                ],
            )

            self.assertEqual(
                out[0].preview_grade,
                "A",
            )

            self.assertTrue(
                lock.call_args.kwargs["dry_run"]
            )

            lock.assert_called_once()

    def test_failure_isolation(self):
        cases = [
            NS(
                guild_id="1",
                rumor_id=i,
                character_id=3,
                state="needs_grade",
                explanation="",
            )
            for i in (1, 2)
        ]

        with (
            patch.object(
                mod,
                "inspect_due_cases",
                return_value=cases,
            ),
            patch.object(
                mod,
                "lock_prepared_grade",
                side_effect=[
                    LookupError("無快照"),
                    NS(
                        dry_run=True,
                        character_id=3,
                        grade="B",
                        score=60,
                    ),
                ],
            ),
        ):
            out = mod.preview_due_cases(
                "1",
                trusted=True,
            )

            self.assertEqual(
                [x.result for x in out],
                [
                    "blocked",
                    "ready_for_review",
                ],
            )

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            mod.preview_due_cases(
                "1",
                trusted=True,
                limit=0,
            )


if __name__ == "__main__":
    unittest.main()
