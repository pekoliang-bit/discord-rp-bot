"""傳聞入口靜態回歸檢查，無需 Discord Token / DB。"""
from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RumorRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "modules" / "pr_rumor.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_player_can_create_without_company_permission(self):
        func = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_rumor")
        body = ast.get_source_segment(self.source, func)
        self.assertIn("own_character", body)
        self.assertIn("company_authorized", body)
        self.assertIn("not own_character and not company_authorized", body)
        self.assertIn("rumor_allowed", body)

    def test_category_is_in_modal(self):
        modal = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "RumorModal")
        self.assertTrue(any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "category" for t in n.targets) for n in modal.body))

    def test_rumor_auto_payout_disabled_by_default(self):
        scheduler = next(n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "RumorScheduler")
        method = next(n for n in scheduler.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "check_due")
        source = ast.get_source_segment(self.source, method)
        self.assertIn("RUMOR_FINALIZATION_ENABLED", source)
        self.assertIn('"0"', source)

if __name__ == "__main__":
    unittest.main()
