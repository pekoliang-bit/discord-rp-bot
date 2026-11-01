"""玩家公關評級文字的三種狀態與異常資料測試。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "core" / "rumor_pr_grade_display.py"
spec = importlib.util.spec_from_file_location("pr_grade_display_under_test", SOURCE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def item(**changes):
    data = dict(rumor_id=9, character_id=1, action_count=2, action_score=175,
                stage="pending", case_status="active", final_grade=None,
                final_score=None, grade_rule_version=None)
    data.update(changes)
    return SimpleNamespace(**data)


class GradeDisplayTests(unittest.TestCase):
    def test_pending_has_no_grade(self):
        message = module.format_pr_grade_status(item())
        self.assertIn("尚未核定", message)
        self.assertIn("175", message)

    def test_locked(self):
        message = module.format_pr_grade_status(item(stage="locked", final_grade="A",
                                                       final_score=76, grade_rule_version="v1"))
        self.assertIn("正式公關評級：A", message)
        self.assertIn("v1", message)

    def test_settled(self):
        message = module.format_pr_grade_status(item(stage="settled", case_status="settled",
                                                       final_grade="B", final_score=66))
        self.assertIn("已結案", message)

    def test_invalid_grade_rejected(self):
        with self.assertRaises(RuntimeError):
            module.format_pr_grade_status(item(stage="locked", final_grade="X", final_score=76))

    def test_settled_case_mismatch_rejected(self):
        with self.assertRaises(RuntimeError):
            module.format_pr_grade_status(item(stage="settled", final_grade="A", final_score=76))


if __name__ == "__main__":
    unittest.main()
