
"""品牌熱度及各來源35%上限測試。"""

import unittest

from core.company_brand_heat_rules import (
    calculate_brand_heat,
    heat_source_breakdown,
)


class BrandHeatTests(unittest.TestCase):

    def test_consumption_conversion(self):
        result = calculate_brand_heat(
            target_heat=10000,
            net_consumption=200000,
        )

        self.assertEqual(
            result["raw"]["consumption"],
            200,
        )

    def test_each_source_cap(self):
        result = calculate_brand_heat(
            target_heat=10000,
            net_consumption=99999999,
            promotion=99999,
            cooperation=99999,
            representative=99999,
        )

        self.assertEqual(
            result["source_limit"],
            3500,
        )

        self.assertTrue(
            all(
                value == 3500
                for value in
                result["effective"].values()
            )
        )

    def test_four_sources_combine(self):
        result = calculate_brand_heat(
            target_heat=10000,
            net_consumption=2500000,
            promotion=2500,
            cooperation=2500,
            representative=2500,
        )

        self.assertEqual(
            result["total"],
            10000,
        )
        self.assertTrue(
            result["qualified"]
        )

    def test_one_source_cannot_qualify(self):
        result = calculate_brand_heat(
            target_heat=10000,
            net_consumption=0,
            promotion=1000000,
        )

        self.assertEqual(
            result["total"],
            3500,
        )
        self.assertFalse(
            result["qualified"]
        )

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            calculate_brand_heat(
                10000,
                0,
                promotion=-1,
            )

    def test_breakdown(self):
        result = calculate_brand_heat(
            10000,
            200000,
            promotion=500,
        )

        text = heat_source_breakdown(result)

        self.assertIn(
            "有效總熱度",
            text,
        )
        self.assertIn(
            "宣傳",
            text,
        )


if __name__ == "__main__":
    unittest.main()
