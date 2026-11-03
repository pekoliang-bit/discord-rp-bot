"""不依賴 Discord 套件的回歸檢查：教學只在成功畫面後紀錄。"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'modules'
HOOKS = {
    'character_view.py': [('view_character', '查看')],
    'story_play.py': [('play', '劇情')],
    'malicious_actions_ui.py': [('malicious', '惡意')],
    'z_system_hub.py': [('overview', '總覽'), ('ranking', '排行')],
    'lottery_threads.py': [('draw', '抽獎')],
    'z_tasks_center.py': [('tasks', '任務')],
}


class HookTests(unittest.TestCase):
    def test_hooks_exist_in_every_command(self):
        for file, functions in HOOKS.items():
            tree = ast.parse((ROOT / file).read_text(encoding='utf-8'))
            for name, command in functions:
                with self.subTest(file=file, command=command):
                    matching = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
                    self.assertEqual(len(matching), 1)
                    source = ast.unparse(matching[0])
                    self.assertIn('require_tutorial_channel', source)

    def test_lottery_and_story_training_does_not_play(self):
        for file, name, costly in (
            ('lottery_threads.py', 'draw', 'make_draw'),
            ('story_play.py', 'play', 'open_progress'),
            ('malicious_actions_ui.py', 'malicious', 'resolve_action'),
        ):
            with self.subTest(file=file):
                tree=ast.parse((ROOT/file).read_text(encoding='utf-8'))
                method=next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name==name)
                training=next(n for n in method.body if isinstance(n,ast.If) and 'tutorial_character is not None' in ast.unparse(n.test))
                self.assertNotIn(costly, ast.unparse(training))
                self.assertTrue(any(isinstance(n,ast.Return) for n in training.body))

    def test_no_unauthorized_owner_commands(self):
        names=(ROOT/'task_newbie.py').read_text(encoding='utf-8')
        self.assertIn("'抽獎'", names)
        self.assertNotIn("'群主指引'", names)
        self.assertNotIn("'創角'", names)


if __name__ == '__main__':
    unittest.main(verbosity=2)
