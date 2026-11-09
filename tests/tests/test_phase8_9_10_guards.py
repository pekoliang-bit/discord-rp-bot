
"""第八～十部分：正式指令缺漏及通知異常鍵防回歸（無網路、無正式 DB）。"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PhaseGuards(unittest.TestCase):
    def test_24_formal_commands(self):
        from core.official_commands import OFFICIAL_COMMANDS
        self.assertEqual(len(OFFICIAL_COMMANDS), 24)
        for name in ("稱號", "新手指引", "新人禮包管理"):
            self.assertNotIn(name, OFFICIAL_COMMANDS)

    def test_missing_slash_commands_block_sync(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        node = ast.parse(source)
        self.assertIn('if self.command_report["missing"]:', source)
        self.assertLess(source.index('if self.command_report["missing"]:'),
                        source.index('synced_global = await self.tree.sync()'))

    def test_notification_bulletin_identifier_fullmatch(self):
        source = (ROOT / "modules/notifications.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("re.fullmatch(r'bulletin-[0-9]+'", source)
        self.assertTrue(re.fullmatch(r"bulletin-[0-9]+", "bulletin-125"))
        for invalid in ("bulletin-", "bulletin-xxx", "bulletin-1-malformed"):
            self.assertIsNone(re.fullmatch(r"bulletin-[0-9]+", invalid))

    def test_company_faction_and_periodic_entries(self):
        for path in (
            "modules/company_portal.py", "modules/faction_ui.py",
            "modules/leaderboard_runtime.py", "modules/notifications.py",
            "modules/lottery_threads.py", "modules/casino_commands.py",
        ):
            with self.subTest(path=path):
                ast.parse((ROOT / path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
