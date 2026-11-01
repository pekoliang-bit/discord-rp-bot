
"""品牌升級規則測試。"""

import unittest

from datetime import datetime, timezone

from core.company_brand_rules import (
    REQUIREMENTS,
    next_brand_level,
    allowed_brand_level,
    enough_age,
    evaluate_brand_upgrade,
)


class BrandRulesTests(unittest.TestCase):

    def test_upgrade_requirements(self):
        self.assertEqual(
            REQUIREMENTS["小資"],
            (10000, 200000),
        )
        self.assertEqual(
            REQUIREMENTS["輕奢"],
            (20000, 1000000),
        )
        self.assertEqual(
            REQUIREMENTS["奢侈"],
            (100000, 5000000),
        )
        self.assertEqual(
            REQUIREMENTS["高奢"],
            (1000000, 25000000),
        )

    def test_upgrade_one_level(self):
        self.assertEqual(
            next_brand_level("平價"),
            "小資",
        )
        self.assertEqual(
            next_brand_level("奢侈"),
            "高奢",
        )
        self.assertIsNone(
            next_brand_level("高奢")
        )

    def test_restaurant_maximum(self):
        self.assertTrue(
            allowed_brand_level("餐廳", "奢侈")
        )
        self.assertFalse(
            allowed_brand_level("餐廳", "高奢")
        )

    def test_design_high_luxury(self):
        self.assertTrue(
            allowed_brand_level("珠寶設計", "高奢")
        )

    def test_14_days(self):
        now = datetime(
            2026, 11, 2, tzinfo=timezone.utc
        )

        self.assertTrue(
            enough_age(
                "2026-10-19 00:00:00",
                now=now,
            )
        )

        self.assertFalse(
            enough_age(
                "2026-10-20 00:00:00",
                now=now,
            )
        )

    def test_timezone_parsing(self):
        now = datetime(
            2026, 11, 2, tzinfo=timezone.utc
        )

        self.assertTrue(
            enough_age(
                "2026-10-19T08:00:00+08:00",
                now=now,
            )
        )

    def test_revenue_shortfall(self):
        result = evaluate_brand_upgrade(
            "平價",
            "服裝設計",
            "2026-10-01",
            10000,
            199999,
            now=datetime(
                2026, 11, 2,
                tzinfo=timezone.utc,
            ),
        )

        self.assertFalse(result["eligible"])
        self.assertIn(
            "30天淨營業額不足",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
