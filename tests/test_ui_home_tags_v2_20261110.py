"""官方首頁與標籤下拉分層靜態驗證。"""
import ast
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
class HomeTagTests(unittest.TestCase):
    def test_sources_parse(self):
        for name in ('modules/official_center.py','modules/tag_admin_ui.py','modules/zz_awards.py','modules/zz_shop_relationships.py','modules/rumor_pr_admin_ui.py'):
            with self.subTest(name=name):
                ast.parse((ROOT/name).read_text(encoding='utf-8-sig'))
    def test_home_layout(self):
        s=(ROOT/'modules/official_center.py').read_text()
        for token in ('arrange_official_home','("正式開群", 2)','class OfficialHome'):
            self.assertIn(token,s)
    def test_tag_effect_is_pending(self):
        s=(ROOT/'modules/tag_admin_ui.py').read_text()
        for token in ('class CategorySelect','class AddTags','relative_percent',"status='pending'",'返回內容建立'):
            self.assertIn(token,s)
    def test_other_buttons_have_relayout(self):
        self.assertIn('arrange_official_home',(ROOT/'modules/zz_awards.py').read_text())
        self.assertIn('AdminToolsView',(ROOT/'modules/zz_shop_relationships.py').read_text())
if __name__=='__main__':unittest.main()
