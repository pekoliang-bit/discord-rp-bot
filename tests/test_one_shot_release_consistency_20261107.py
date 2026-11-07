
"""Regression contracts for the consolidated 2026-11-07 release.

No Discord login and no writes to the player's database.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseConsistencyTests(unittest.TestCase):
    def test_extra_character_status_uses_existing_levels(self):
        source = (ROOT / 'modules' / 'character_view.py').read_text(encoding='utf-8')
        self.assertIn('effective_levels(actor.guild_id)', source)
        self.assertNotIn('from modules.extra_character_store import _world_levels', source)
        self.assertIn('lv = max(1, min(len(levels), lv))', source)

    def test_approved_comment_library_is_complete(self):
        from core.comment_library import seed_catalog, SEED_PATH
        rows = seed_catalog(SEED_PATH)['approved']
        self.assertEqual(len(rows), 960)
        self.assertEqual(len({r['id'] for r in rows}), 960)
        for cid in range(1, 13):
            self.assertEqual(sum(r['category_id'] == cid for r in rows), 80)

    def test_reward_gates_stay_closed(self):
        from modules.leaderboard_payout_policy import inspect_reward, PAYOUT_ENABLED
        self.assertFalse(PAYOUT_ENABLED)
        reward = dict(board='industry', rank=1, coins=1, values={'business_heat': 5},
                      requires_adapter=True, reward_version='v11-approved')
        result = inspect_reward('industry', 'lbentity:company:1:1', reward)
        self.assertFalse(result['allowed'])
        self.assertIn('非角色型', '；'.join(result['issues']))
        self.assertIn('適配器', '；'.join(result['issues']))

    def test_no_undefined_local_symbol_imports(self):
        files = {}
        for folder in ('core', 'modules'):
            for path in (ROOT / folder).glob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                mod = f'{folder}.{path.stem}'
                names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
                for node in tree.body:
                    if isinstance(node, ast.ImportFrom):
                        names.update(n.asname or n.name for n in node.names)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                            if isinstance(target, ast.Name):
                                names.add(target.id)
                files[mod] = (tree, names)
        missing = []
        for importer, (tree, _) in files.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in files:
                    for item in node.names:
                        if item.name != '*' and item.name not in files[node.module][1]:
                            missing.append(f'{importer}: {node.module}.{item.name}')
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
