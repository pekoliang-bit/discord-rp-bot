
"""唯讀批次預覽測試，不需 Discord 或正式資料庫。"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

for name in (
    "core",
    "core.rumor_settlement_queue",
    "core.rumor_grade_pipeline",
):
    sys.modules.setdefault(
        name, types.ModuleType(name)
    )

sys.modules[
    "core.rumor_settlement_queue"
].inspect_due_cases = lambda *a, **kw: []

sys.modules[
    "core.rumor_grade_pipeline"
].lock_prepared_grade = lambda *a, **kw: None

spec = importlib.util.spec_from_file_location(
    "rumor_due_preview_tested",
    ROOT / "core" / "rumor_due_preview.py",
)

mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


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
                "1", trusted=True
            )

            self.assertEqual(
                [x.result for x in out],
                ["ready_for_review", "skipped"],
            )

            self.assertEqual(
                out[0].preview_grade, "A"
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
                "1", trusted=True
            )

            self.assertEqual(
                [x.result for x in out],
                ["blocked", "ready_for_review"],
            )

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            mod.preview_due_cases(
                "1", trusted=True, limit=0
            )


if __name__ == "__main__":
    unittest.main()
