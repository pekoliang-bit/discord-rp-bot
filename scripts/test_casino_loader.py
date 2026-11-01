
"""娛樂城載入與指令註冊的唯讀回歸測試。

執行：
python -m unittest discover -s scripts \
    -p 'test_casino_loader.py' -v

無須安裝 discord.py，也不連接資料庫。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent),
)

import casino_integration_check as checker


class CasinoLoaderTests(unittest.TestCase):

    def test_all_casino_files_present(self):
        for name in checker.REQUIRED:
            with self.subTest(name=name):
                self.assertTrue(
                    (
                        MODULES / (name + ".py")
                    ).is_file()
                )

    def test_casino_only_one_extension(self):
        extensions = []

        for path in MODULES.glob("casino_*.py"):
            tree = ast.parse(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )

            if checker.top_level_setup(tree):
                extensions.append(path.stem)

        self.assertEqual(
            extensions,
            ["casino_commands"],
        )

    def test_only_one_casino_slash_command(self):
        owners = []

        for path in MODULES.glob("*.py"):
            tree = ast.parse(
                path.read_text(
                    encoding="utf-8-sig"
                )
            )

            names = checker.command_names(tree)

            if "娛樂城" in names:
                owners.append(path.stem)

            self.assertNotIn(
                "娛樂城排行",
                names,
            )

        self.assertEqual(
            owners,
            ["casino_commands"],
        )

    def test_loader_has_safe_filter(self):
        main_tree = ast.parse(
            (
                ROOT / "main.py"
            ).read_text(
                encoding="utf-8-sig"
            )
        )

        self.assertEqual(
            checker.check_loader(main_tree),
            [],
        )

    def test_commands_have_character_lookup(self):
        tree = ast.parse(
            (
                MODULES / "casino_commands.py"
            ).read_text(
                encoding="utf-8-sig"
            )
        )

        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            )
        }

        self.assertIn("_characters", names)
        self.assertIn("setup", names)

    def test_integration_checker_passes(self):
        self.assertEqual(
            checker.inspect_project(),
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
