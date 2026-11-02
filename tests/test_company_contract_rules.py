
import unittest

from datetime import datetime, timezone

from core.company_contract_rules import (
    validate_contract,
    self_company_exit_status,
    requires_penalty,
)


class ContractRulesTests(unittest.TestCase):

    def test_valid_contract(self):
        validate_contract(5000, 7, 10000)
        validate_contract(8000, 14, 0)

    def test_invalid_cycle(self):
        with self.assertRaises(ValueError):
            validate_contract(5000, 30, 0)

    def test_negative_penalty(self):
        with self.assertRaises(ValueError):
            validate_contract(5000, 7, -1)

    def test_five_day_grace(self):
        status = self_company_exit_status(
            "2026-11-01T00:00:00+00:00",
            now=datetime(
                2026, 11, 5,
                tzinfo=timezone.utc,
            ),
        )

        self.assertTrue(status["within_grace"])

    def test_five_day_overdue(self):
        status = self_company_exit_status(
            "2026-11-01T00:00:00+00:00",
            now=datetime(
                2026, 11, 7,
                tzinfo=timezone.utc,
            ),
        )

        self.assertTrue(status["overdue"])

    def test_penalty_conditions(self):
        self.assertTrue(
            requires_penalty(10000)
        )
        self.assertFalse(
            requires_penalty(
                10000,
                mutual_release=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
