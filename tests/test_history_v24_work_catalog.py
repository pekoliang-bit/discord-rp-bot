import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.history_v24_work_catalog import validate_works


class TestWorks(unittest.TestCase):
    def test_valid_no_rewards(self):
        data = [{"asset_id": "WN-0001", "title": "測試用占位作品", "rarity": "R2", "meta": {
            "work_type": "院線電影", "occupations": ["演員"], "roles": ["主演"]}}]
        report = validate_works(data)
        self.assertTrue(report.valid)
        self.assertEqual(report.type_counts, {"電影": 1})

    def test_duplicate_and_reward_rejected(self):
        obj = {"asset_id": "WN-0001", "title": "測試", "meta": {
            "work_type": "綜藝", "occupations": ["主持人"], "roles": ["常駐"]}, "reward": 10}
        report = validate_works([obj, obj])
        self.assertFalse(report.valid)
        self.assertTrue(any("重複" in e for e in report.errors))
        self.assertTrue(any("數值加成" in e for e in report.errors))

    def test_empty_is_not_import(self):
        result = validate_works([])
        self.assertTrue(result.valid)
        self.assertEqual(result.total, 0)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
