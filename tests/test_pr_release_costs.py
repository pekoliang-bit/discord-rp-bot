
"""正式 T1~T5 價格與重複效果契約。"""

import unittest

from core.rumor_payment import (
    PR_COSTS,
    REPEAT_MULTIPLIERS,
)


class CostContract(unittest.TestCase):

    def test_exact_costs(self):
        self.assertEqual(
            PR_COSTS,
            {
                1: (1000, 1),
                2: (5000, 2),
                3: (15000, 3),
                4: (50000, 5),
                5: (100000, 8),
            },
        )

    def test_repeat_effect(self):
        self.assertEqual(
            REPEAT_MULTIPLIERS,
            (1.0, 0.75, 0.50, 0.25, 0.10),
        )

    def test_monotonic_costs(self):
        for position in (0, 1):
            values = [
                PR_COSTS[tier][position]
                for tier in range(1, 6)
            ]

            self.assertEqual(
                values,
                sorted(values),
            )

            self.assertEqual(
                len(set(values)),
                5,
            )


if __name__ == "__main__":
    unittest.main()
