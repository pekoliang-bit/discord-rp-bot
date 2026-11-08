
"""無 Discord 依賴的申請能力與第一部分防回歸驗收。"""
import ast
from pathlib import Path
import unittest

from core.application_capabilities import (
    GENERAL_AUTO_EXECUTORS, GENERAL_PENDING_EXECUTORS,
    general_can_approve, general_status_text, SPECIAL_ROUTES,
)

ROOT = Path(__file__).resolve().parents[1]


class ApplicationCapabilityTests(unittest.TestCase):
    def test_known_kinds_disjoint(self):
        self.assertFalse(GENERAL_AUTO_EXECUTORS & GENERAL_PENDING_EXECUTORS)

    def test_pending_cannot_be_approved(self):
        for kind in GENERAL_PENDING_EXECUTORS:
            with self.subTest(kind=kind):
                self.assertFalse(general_can_approve(kind))
                self.assertIn("不可核准", general_status_text(kind))

    def test_live_handlers_can_be_approved(self):
        for kind in GENERAL_AUTO_EXECUTORS:
            self.assertTrue(general_can_approve(kind))

    def test_unknown_fails_closed(self):
        self.assertFalse(general_can_approve("unrecognized"))

    def test_all_general_categories_classified(self):
        module = ast.parse((ROOT / "modules/application_extras.py").read_text(encoding="utf-8"))
        categories = next(node.value for node in module.body
                          if isinstance(node, ast.Assign) and any(
                              isinstance(t, ast.Name) and t.id == "CATEGORIES" for t in node.targets))
        names = {key.value for key in categories.keys}
        self.assertEqual(names, GENERAL_AUTO_EXECUTORS | GENERAL_PENDING_EXECUTORS)

    def test_special_approval_routes_documented(self):
        self.assertIn("report_private", SPECIAL_ROUTES)
        self.assertIn("report_appeal", SPECIAL_ROUTES)
        self.assertIn("loan", SPECIAL_ROUTES)

    def test_official_commands_have_24_and_exclude_removed(self):
        from core.official_commands import OFFICIAL_COMMANDS, OFFICIAL_COMMAND_NAMES
        names = set(OFFICIAL_COMMAND_NAMES)
        self.assertEqual(names, set(OFFICIAL_COMMANDS))
        self.assertIn("from core.official_commands import OFFICIAL_COMMANDS",
                      (ROOT / "main.py").read_text(encoding="utf-8"))
        self.assertEqual(len(names),24)
        self.assertTrue({"申請","審核","任務","資產"} <= names)
        self.assertFalse({"稱號","新手指引","新人禮包管理","作品"} & names)


if __name__ == "__main__":
    unittest.main()
