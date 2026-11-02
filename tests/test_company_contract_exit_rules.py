
"""公司解約期限及違約金規則測試。"""

import unittest
from datetime import datetime, timezone

from core.company_contract_exit_rules import (
    is_self_company_grace,
    calculate_exit_penalty,
)


class ExitRulesTests(unittest.TestCase):

    def test_within_five_days(self):
        self.assertTrue(
            is_self_company_grace(
                "2026-11-01T00:00:00+00:00",
                "2026-10-01T00:00:00+00:00",
                now=datetime(
                    2026, 11, 5,
                    tzinfo=timezone.utc,
                ),
            )
        )

    def test_after_five_days(self):
        self.assertFalse(
            is_self_company_grace(
                "2026-11-01T00:00:00+00:00",
                "2026-10-01T00:00:00+00:00",
                now=datetime(
                    2026, 11, 7,
                    tzinfo=timezone.utc,
                ),
            )
        )

    def test_company_before_employment(self):
        self.assertFalse(
            is_self_company_grace(
                "2026-09-01",
                "2026-10-01",
                now=datetime(
                    2026, 11, 2,
                    tzinfo=timezone.utc,
                ),
            )
        )

    def test_ordinary_penalty(self):
        self.assertEqual(
            calculate_exit_penalty(
                50000, "ordinary"
            ),
            50000,
        )

    def test_mutual_release(self):
        self.assertEqual(
            calculate_exit_penalty(
                50000,
                "mutual",
                mutual_approved=True,
            ),
            0,
        )

    def test_unapproved_mutual_blocked(self):
        with self.assertRaises(PermissionError):
            calculate_exit_penalty(
                50000, "mutual"
            )

    def test_self_company_exemption(self):
        self.assertEqual(
            calculate_exit_penalty(
                50000,
                "self_company",
                self_company_verified=True,
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
