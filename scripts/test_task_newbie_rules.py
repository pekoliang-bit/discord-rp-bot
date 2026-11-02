"""唯讀新手任務規則回歸測試；無 Discord、DB 連線。"""
import ast
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / 'modules'

def tree(name):
    return ast.parse((ROOT / name).read_text(encoding='utf-8-sig'))

class TaskRulesTest(unittest.TestCase):
    def test_syntax_all_updated_modules(self):
        for f in ('task_newbie.py','task_event_bridge.py','z_tasks_center.py','task_service.py',
                  'task_conditions.py','task_daily.py','task_admin.py','task_defaults.py','task_repository.py'):
            with self.subTest(f=f): self.assertIsInstance(tree(f), ast.Module)
    def test_exact_sixteen_beginner_commands(self):
        t=tree('task_newbie.py')
        node=next(x for x in t.body if isinstance(x,ast.Assign) and any(isinstance(z,ast.Name) and z.id=='REQUIRED_COMMANDS' for z in x.targets))
        commands=ast.literal_eval(node.value)
        self.assertEqual(len(commands),16)
        self.assertNotIn('創角', commands)
        self.assertNotIn('群主指引', commands)
    def test_no_new_admin_slash(self):
        source=ast.unparse(tree('task_admin.py'))
        self.assertNotIn('app_commands.command',source)
    def test_success_is_explicit(self):
        source=ast.unparse(tree('task_event_bridge.py'))
        self.assertIn('completed_tutorial_view',source)
        self.assertIn('新手指令待成功確認',source)

if __name__=='__main__': unittest.main(verbosity=2)
