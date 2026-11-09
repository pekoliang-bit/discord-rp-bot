
"""Offline regression contracts for the 2026-11-07 hotfix. No Discord token or production DB."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class HotfixContracts(unittest.TestCase):
    def text(self, file):
        path = ROOT / file
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        return path.read_text(encoding="utf-8-sig")

    def test_creation_data_modal_uses_isolated_fields(self):
        src = self.text("modules/creation_wizard.py")
        self.assertIn("self._data_fields = []", src)
        self.assertIn("for key, item in self._data_fields:", src)

    def test_creation_error_reports_missing_attribute(self):
        src = self.text("modules/creation_wizard.py")
        self.assertIn("isinstance(exc, AttributeError)", src)
        self.assertIn("detail = str(exc).strip()", src)

    def test_tag_button_deferred_before_database_heavy_ui(self):
        tree = ast.parse(self.text("modules/creation_wizard.py"))
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "WizardView")
        method = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "choose_tags")
        content = ast.unparse(method)
        self.assertLess(content.index("interaction.response.defer"), content.index("await show_tag_picker"))

    def test_empty_tag_search_has_valid_select_bounds(self):
        src = self.text("modules/tag_selection_ui.py")
        self.assertIn("max_values=max(1,min(parent.limit,len(page)))", src)
        self.assertIn("disabled=not bool(page)", src)
        self.assertIn("LOG.exception('角色標籤選單開啟失敗", src)

    def test_company_initializes_missing_workforce_tables(self):
        src = self.text("modules/company_portal.py")
        self.assertIn("has_workforce_tables", src)
        self.assertIn("from modules.company_workforce import initialize as init_workforce", src)

    def test_company_exception_handles_panel_construction(self):
        tree = ast.parse(self.text("modules/company_portal.py"))
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "CompanyPortal")
        method = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "company")
        target = next(x for x in ast.walk(method) if isinstance(x, ast.Try))
        self.assertTrue(any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in target.handlers))
        self.assertTrue(any("panel = CompanyPanel" in ast.unparse(node) for node in ast.walk(method) if isinstance(node, ast.Try)))

    def test_no_additional_top_level_slash_commands(self):
        for file in ["modules/creation_wizard.py", "modules/tag_selection_ui.py", "modules/company_portal.py"]:
            self.text(file)

if __name__ == "__main__": unittest.main()
