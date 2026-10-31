
"""完全隔離的工作單執行器測試，不讀寫正式資料庫。"""

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
    "core.database",
    "core.rumor_plan_store",
    "modules",
    "modules.rumor_settlement",
):
    sys.modules.setdefault(name, types.ModuleType(name))


class DB:
    row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        return self

    def fetchone(self):
        return self.row


db = DB()

sys.modules[
    "core.database"
].get_connection = lambda: db

sys.modules[
    "core.rumor_plan_store"
].load_reward_plan = lambda *args, **kwargs: NS(
    grade="A",
    changes={"路人緣": 5},
)

sys.modules[
    "modules.rumor_settlement"
].settle_character_reward = lambda *args, **kwargs: NS(
    state="applied",
    duplicate=False,
)

path = ROOT / "core" / "rumor_reward_job_executor.py"

spec = importlib.util.spec_from_file_location(
    "executor_target", path
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def setUp(self):
        db.row = {
            "status": "active",
            "grade": "A",
            "changes_json": '{"路人緣":5}',
            "state": "prepared",
        }

    def test_permission(self):
        with self.assertRaises(PermissionError):
            module.execute_locked_reward_job("1", 2, 3)

    def test_preview_no_payment(self):
        with patch.object(
            module, "settle_character_reward"
        ) as fn:
            result = module.execute_locked_reward_job(
                "1", 2, 3, trusted=True
            )
            self.assertTrue(result.dry_run)
            fn.assert_not_called()

    def test_mismatched_plan(self):
        db.row["changes_json"] = '{"路人緣":7}'
        with self.assertRaises(RuntimeError):
            module.execute_locked_reward_job(
                "1", 2, 3, trusted=True
            )

    def test_applied_skips(self):
        db.row["state"] = "applied"
        with patch.object(
            module, "settle_character_reward"
        ) as fn:
            result = module.execute_locked_reward_job(
                "1", 2, 3,
                trusted=True,
                dry_run=False,
            )
            self.assertTrue(result.duplicate)
            fn.assert_not_called()

    def test_explicit_execute(self):
        with patch.object(
            module,
            "settle_character_reward",
            return_value=NS(
                state="applied",
                duplicate=False,
            ),
        ) as fn:
            result = module.execute_locked_reward_job(
                "1", 2, 3,
                trusted=True,
                dry_run=False,
            )
            self.assertEqual(result.state, "applied")
            fn.assert_called_once()

    def test_settled_pending_blocked(self):
        db.row["status"] = "settled"
        with self.assertRaises(RuntimeError):
            module.execute_locked_reward_job(
                "1", 2, 3,
                trusted=True,
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
