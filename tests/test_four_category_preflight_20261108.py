"""四系統可部署前靜態契約；不連線、也不更改玩家資料。"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from core.network_post_categories import SUGGESTED_TAGS, classify_forum_tags

ROOT = Path(__file__).resolve().parents[1]


class FourCategoryPreflightTests(unittest.TestCase):
    def test_four_player_command_extensions_are_present(self):
        for name in ('company_portal', 'rumor_interface',
                     'location_action_commands', 'live_stream_ui'):
            with self.subTest(extension=name):
                path = ROOT / 'modules' / f'{name}.py'
                tree = ast.parse(path.read_text(encoding='utf-8-sig'))
                self.assertTrue(any(isinstance(node, ast.AsyncFunctionDef)
                                    and node.name == 'setup' for node in tree.body))
                self.assertTrue(any(isinstance(node, ast.ClassDef)
                                    for node in tree.body))

    def test_forum_categories_are_separate_from_social_storage(self):
        src = (ROOT / 'core/network_post_categories.py').read_text(encoding='utf-8')
        self.assertNotIn('CREATE TABLE', src)
        self.assertNotIn('get_connection', src)
        self.assertNotIn('transaction(', src)
        self.assertEqual(len(SUGGESTED_TAGS), 12)
        self.assertEqual(len(set(SUGGESTED_TAGS)), 12)
        self.assertEqual(classify_forum_tags(['日常生活', '商業活動']), (1, 4))
        self.assertEqual(classify_forum_tags(['🎬 影視作品', '自訂活動']), (2,))
        self.assertEqual(classify_forum_tags(['自訂活動']), ())
        self.assertEqual(classify_forum_tags(['商業活動', '商業活動']), (4,))

    def test_forum_ui_uses_authoritative_categories(self):
        src = (ROOT / 'modules/network_forum_ui.py').read_text(encoding='utf-8')
        self.assertIn('from core.network_post_categories import classify_forum_tags', src)
        self.assertIn('classify_forum_tags(', src)
        self.assertIn('forum.available_tags', src)
        self.assertIn('social.save_post', src)

    def test_action_catalog_stays_approval_gated(self):
        src = (ROOT / 'modules/action_reward_rules.py').read_text(encoding='utf-8')
        self.assertIn('a.get("approved") is True', src)
        self.assertIn('RESTRICTED_DIRECT_STATS', src)
        self.assertIn('影響指數', src)

    def test_rumor_and_social_event_entrypoints(self):
        rumor = (ROOT / 'modules/rumor_interface.py').read_text(encoding='utf-8')
        stream = (ROOT / 'modules/live_stream_ui.py').read_text(encoding='utf-8')
        self.assertIn('RumorPlayerPanel', rumor)
        self.assertIn('async def on_message(', stream)
        self.assertIn('async def on_raw_reaction_add(', stream)
        self.assertIn('record_interaction', stream)

    def test_no_live_token_or_production_db_in_preflight(self):
        script = (ROOT / 'scripts/check_four_categories_preflight_20261108.py').read_text('utf-8')
        self.assertIn('subprocess.run', script)
        self.assertNotIn('discord.Client(', script)
        self.assertNotIn('bot.run(', script)


if __name__ == '__main__':
    unittest.main()
