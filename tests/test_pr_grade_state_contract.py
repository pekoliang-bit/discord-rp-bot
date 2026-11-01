
"""玩家端必須區分暫定分數、正式評級與結案。"""

import unittest

from types import SimpleNamespace as NS

from core.rumor_pr_grade_display import (
    format_pr_grade_status,
)


class GradeContract(unittest.TestCase):

    def make(self, **overrides):
        values = dict(
            rumor_id=8,
            character_id=3,
            action_count=2,
            action_score=45,
            stage="pending",
            case_status="active",
            final_grade=None,
            final_score=None,
            grade_rule_version=None,
        )

        values.update(overrides)
        return NS(**values)

    def test_pending_no_official_grade(self):
        text = format_pr_grade_status(
            self.make()
        )

        self.assertIn(
            "尚未核定",
            text,
        )

        self.assertNotIn(
            "正式公關評級：A",
            text,
        )

    def test_locked_shows_snapshot(self):
        text = format_pr_grade_status(
            self.make(
                stage="locked",
                final_grade="A",
                final_score=80,
                grade_rule_version="v1",
            )
        )

        self.assertIn(
            "正式公關評級：A",
            text,
        )

        self.assertIn(
            "尚未結案",
            text,
        )

    def test_settled_requires_settled_case(self):
        with self.assertRaises(RuntimeError):
            format_pr_grade_status(
                self.make(
                    stage="settled",
                    final_grade="A",
                    final_score=80,
                )
            )

    def test_settled_display(self):
        text = format_pr_grade_status(
            self.make(
                stage="settled",
                case_status="settled",
                final_grade="S",
                final_score=95,
            )
        )

        self.assertIn(
            "已結案",
            text,
        )

        self.assertIn(
            "正式公關評級：S",
            text,
        )


if __name__ == "__main__":
    unittest.main()
