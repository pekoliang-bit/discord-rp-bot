"""管理介面分層與返回導覽：離線 AST 合約，不需連接 Discord。"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tree(relative):
    path = ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def view(relative, name):
    return next(c for c in tree(relative).body if isinstance(c, ast.ClassDef) and c.name == name)


def member_names(cls):
    return {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def sections(cls):
    assignment = next(n for n in cls.body if isinstance(n, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id in ("SECTION_BUTTONS", "SECTIONS") for target in n.targets))
    return ast.literal_eval(assignment.value)


class NavigationTests(unittest.TestCase):
    def test_tasks_are_compact_and_every_section_has_back(self):
        cls = view('modules/task_admin.py', 'TaskAdminView')
        groups = sections(cls)
        self.assertEqual(set(groups), {'tasks', 'review', 'variety', 'operations'})
        self.assertEqual(len(set(sum((list(x) for x in groups.values()), []))), 15)
        self.assertLessEqual(max(len(items) for items in groups.values()), 5)
        self.assertTrue({'_arrange', '_add_navigation', 'interaction_check'} <= member_names(cls))
        self.assertIn('返回內容建立', ast.unparse(cls))
        self.assertIn('返回任務管理', ast.unparse(cls))
        self.assertIn('VarietyTaskAdminView', ast.unparse(cls))

    def test_titles_have_three_sections_with_back(self):
        cls = view('modules/achievement_admin_ui.py', 'AchievementAdminView')
        groups = sections(cls)
        self.assertEqual(set(groups), {'catalog', 'create', 'legend'})
        self.assertEqual(len(set(sum((list(x) for x in groups.values()), []))), 9)
        self.assertIn('返回稱號管理', ast.unparse(cls))
        self.assertIn('返回內容建立', ast.unparse(cls))

    def test_npcs_have_two_sections_and_back(self):
        cls = view('modules/extra_character_manager.py', 'ExtraCharacterView')
        groups = sections(cls)
        self.assertEqual(set(groups), {'resources', 'production'})
        self.assertEqual(len(set(sum((list(x) for x in groups.values()), []))), 8)
        self.assertIn('返回路人管理', ast.unparse(cls))
        self.assertIn('返回內容建立', ast.unparse(cls))

    def test_official_staff_and_records_have_return(self):
        for file, clsname in (
            ('modules/official_center.py', 'PendingView'),
            ('modules/official_center.py', 'GrantsView'),
            ('modules/official_center.py', 'StaffApplyView'),
            ('modules/official_center.py', 'StaffRosterView'),
            ('modules/content_builder.py', 'ContentRecordsView'),
        ):
            with self.subTest(view=clsname):
                cls = view(file, clsname)
                self.assertTrue(any('back' in n for n in member_names(cls)))
                self.assertIn('response.edit_message', ast.unparse(cls))

    def test_no_new_slash_commands(self):
        for file in ('modules/task_admin.py', 'modules/achievement_admin_ui.py',
                     'modules/extra_character_manager.py', 'modules/official_center.py',
                     'modules/content_builder.py'):
            source = (ROOT / file).read_text(encoding='utf-8-sig')
            self.assertNotIn('@app_commands.command(', source if file != 'modules/official_center.py' else '')


if __name__ == '__main__':
    unittest.main()
