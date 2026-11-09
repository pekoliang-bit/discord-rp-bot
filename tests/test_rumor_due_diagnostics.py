
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


class FakeLoop:
    def __call__(self, func):
        func.before_loop = lambda f: f
        return func


# 把假的 Discord 與假排程佇列限制在目標模組的匯入階段，
# 避免全套測試被污染；目標模組在匯入後已持有測試依賴的參考。
fake_discord = types.ModuleType("discord")
fake_ext = types.ModuleType("discord.ext")
fake_commands = types.ModuleType("discord.ext.commands")
fake_tasks = types.ModuleType("discord.ext.tasks")
fake_commands.Cog = object
fake_tasks.loop = lambda **kwargs: FakeLoop()
fake_ext.commands = fake_commands
fake_ext.tasks = fake_tasks
fake_discord.ext = fake_ext
fake_queue = types.ModuleType("core.rumor_settlement_queue")
fake_queue.inspect_due_cases = lambda *args, **kwargs: []

with patch.dict(sys.modules, {
    "discord": fake_discord,
    "discord.ext": fake_ext,
    "discord.ext.commands": fake_commands,
    "discord.ext.tasks": fake_tasks,
    "core.rumor_settlement_queue": fake_queue,
}):
    spec = importlib.util.spec_from_file_location(
        "rumor_due_diagnostics_under_test",
        ROOT / "modules" / "rumor_due_diagnostics.py",
    )
    module = importlib.util.module_from_spec(spec)
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
