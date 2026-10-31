
"""不連接 Discord 或正式資料庫的唯讀巡檢測試。"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

for name in (
    "discord",
    "discord.ext",
    "discord.ext.commands",
    "discord.ext.tasks",
    "core",
    "core.rumor_settlement_queue",
):
    sys.modules.setdefault(
        name, types.ModuleType(name)
    )


class FakeLoop:
    def __call__(self, func):
        func.before_loop = lambda f: f
        return func


sys.modules["discord.ext.commands"].Cog = object
sys.modules["discord.ext.tasks"].loop = (
    lambda **kw: FakeLoop()
)

sys.modules["discord.ext"].commands = (
    sys.modules["discord.ext.commands"]
)
sys.modules["discord.ext"].tasks = (
    sys.modules["discord.ext.tasks"]
)

sys.modules[
    "core.rumor_settlement_queue"
].inspect_due_cases = lambda *a, **kw: []

spec = importlib.util.spec_from_file_location(
    "rumor_due_diagnostics_under_test",
    ROOT / "modules" / "rumor_due_diagnostics.py",
)

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class DiagnosticsTests(unittest.TestCase):

    def test_disabled_reads_nothing(self):
        with patch.dict(
            os.environ,
            {"RUMOR_DIAGNOSTICS_ENABLED": "0"},
        ):
            with patch.object(
                module, "inspect_due_cases"
            ) as scan:
                self.assertEqual(
                    module.inspect_once(), {}
                )
                scan.assert_not_called()

    def test_enabled_summarizes_without_writing(self):
        rows = [
            SimpleNamespace(state="needs_grade"),
            SimpleNamespace(state="needs_plan"),
            SimpleNamespace(state="needs_grade"),
        ]

        with patch.dict(
            os.environ,
            {"RUMOR_DIAGNOSTICS_ENABLED": "1"},
        ):
            with patch.object(
                module,
                "inspect_due_cases",
                return_value=rows,
            ) as scan:
                self.assertEqual(
                    module.inspect_once(),
                    {
                        "needs_grade": 2,
                        "needs_plan": 1,
                    },
                )
                scan.assert_called_once_with(
                    None, limit=100
                )

    def test_invalid_limit_when_enabled(self):
        with patch.dict(
            os.environ,
            {"RUMOR_DIAGNOSTICS_ENABLED": "1"},
        ):
            with self.assertRaises(ValueError):
                module.inspect_once(limit=0)

    def test_no_due_cases(self):
        with patch.dict(
            os.environ,
            {"RUMOR_DIAGNOSTICS_ENABLED": "1"},
        ):
            self.assertEqual(
                module.inspect_once(), {}
            )


if __name__ == "__main__":
    unittest.main()
