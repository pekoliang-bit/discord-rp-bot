
"""7／14天薪資週期單元測試。"""

import unittest

from datetime import datetime, timezone

from core.company_payroll_period import (
    payroll_period,
    payday_text,
)


class PayrollPeriodTests(unittest.TestCase):

    def setUp(self):
        self.hired = "2026-11-01 00:00:00"

    def at(self, day):
        return datetime(
            2026, 11, day,
            tzinfo=timezone.utc,
        )

    def test_not_yet_due(self):
        result = payroll_period(
            self.hired,
            7,
            now=self.at(7),
        )

        self.assertIsNone(result)

    def test_seven_days(self):
        result = payroll_period(
            self.hired,
            7,
            now=self.at(8),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.cycle_days, 7)
        self.assertIn("7天制", payday_text(result))

    def test_fourteen_days(self):
        result = payroll_period(
            self.hired,
            14,
            now=self.at(15),
        )

        self.assertEqual(result.cycle_days, 14)

    def test_no_double_period(self):
        first = payroll_period(
            self.hired,
            7,
            now=self.at(8),
        )

        second = payroll_period(
            self.hired,
            7,
            now=self.at(10),
        )

        self.assertEqual(first.key, second.key)

    def test_next_period(self):
        first = payroll_period(
            self.hired,
            7,
            now=self.at(8),
        )

        second = payroll_period(
            self.hired,
            7,
            now=self.at(15),
        )

        self.assertNotEqual(first.key, second.key)

    def test_invalid_cycle(self):
        with self.assertRaises(ValueError):
            payroll_period(
                self.hired,
                30,
                now=self.at(15),
            )

    def test_timezone(self):
        result = payroll_period(
            "2026-11-01T08:00:00+08:00",
            7,
            now=self.at(8),
        )

        self.assertEqual(
            result.start.hour,
            0,
        )


if __name__ == "__main__":
    unittest.main()
