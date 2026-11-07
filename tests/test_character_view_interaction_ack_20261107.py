"""/查看 component acknowledgement regression (offline, no Discord token)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "modules" / "character_view.py"
TREE = ast.parse(FILE.read_text(encoding="utf-8"))


def find_class(name):
    return next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == name)


def find_method(class_name, name):
    return next(n for n in find_class(class_name).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def call_names(fn):
    names = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            try:
                names.append((node.lineno, ast.unparse(node.func)))
            except Exception:
                pass
    return sorted(names)


class CharacterViewInteractionAckTests(unittest.TestCase):
    def test_all_expensive_entrypoints_acknowledge_first(self):
        targets = [
            ("SearchModal", "on_submit"),
            ("CharacterCategorySelect", "callback"),
            ("CharacterItemSelect", "callback"),
            ("CharacterListView", "move"),
        ]
        for cls, method in targets:
            with self.subTest(cls=cls, method=method):
                calls = call_names(find_method(cls, method))
                defer = next(n for n, name in calls if name == "interaction.response.defer")
                worker = next(n for n, name in calls if name == "asyncio.to_thread")
                self.assertLess(defer, worker)

    def test_my_characters_uses_deferred_response(self):
        method = next(n for n in ast.walk(find_class("CategoryView"))
                      if isinstance(n, ast.AsyncFunctionDef) and n.name == "open_my_characters")
        calls = call_names(method)
        self.assertLess(next(n for n, name in calls if name == "interaction.response.defer"),
                        next(n for n, name in calls if name == "asyncio.to_thread"))

    def test_no_double_acknowledge_in_target_callbacks(self):
        targets = [
            ("SearchModal", "on_submit"),
            ("CharacterCategorySelect", "callback"),
            ("CharacterItemSelect", "callback"),
            ("CharacterListView", "move"),
        ]
        for cls, method in targets:
            with self.subTest(cls=cls, method=method):
                names = [name for _, name in call_names(find_method(cls, method))]
                self.assertNotIn("interaction.response.edit_message", names)
                self.assertIn("_edit_deferred_component", names)

    def test_error_is_ephemeral_after_ack(self):
        fn = next(n for n in TREE.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_component_error")
        names = [name for _, name in call_names(fn)]
        self.assertIn("interaction.response.is_done", names)
        self.assertIn("interaction.followup.send", names)
        self.assertIn("interaction.response.send_message", names)

    def test_existing_view_paging_and_privacy_remain(self):
        source = FILE.read_text(encoding="utf-8")
        for fragment in (
            'label="👤 查看自己的角色"',
            '"mine": "我的角色"',
            'label=\'🌐 公開\'',
            'label=\'🔒 私人\'',
            'label=\'🎯 點數分配\'',
            'label=\'⬅️ 返回\'',
            'visible_biography(settings, private and authorized)',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
