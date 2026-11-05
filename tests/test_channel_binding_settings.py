"""網路/商業分類設定不應因部分更新而丟失；不需連線 Discord。"""
import os
import tempfile
import unittest


class ChannelBindingSettingsTests(unittest.TestCase):
    def setUp(self):
        from core import config, database
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config = config.DATABASE_PATH
        self.old_database = database.DATABASE_PATH
        config.DATABASE_PATH = database.DATABASE_PATH = os.path.join(self.tmp.name, 'bindings.sqlite3')
        database.init_db()

    def tearDown(self):
        from core import config, database
        database.DATABASE_PATH = self.old_database
        config.DATABASE_PATH = self.old_config
        self.tmp.cleanup()

    def test_network_partial_updates_keep_factions(self):
        from core import network_groups as ng
        guild = 123456789012345678
        ng.set_settings(guild, category_id=123456789012345679)
        ng.set_settings(guild, global_channel_id=123456789012345680)
        ng.set_settings(guild, faction_name='單打獨鬥', faction_channel_id=123456789012345681)
        ng.set_settings(guild, global_channel_id=123456789012345682)
        settings = ng.settings(guild)
        self.assertEqual(settings['category_id'], 123456789012345679)
        self.assertEqual(settings['global_channel_id'], 123456789012345682)
        self.assertEqual(settings['factions']['單打獨鬥'], 123456789012345681)

    def test_builtin_defaults_survive_single_update(self):
        from core import network_groups as ng
        ng.set_settings(ng.DEFAULT_GUILD, category_id=123456789012345683)
        settings = ng.settings(ng.DEFAULT_GUILD)
        self.assertEqual(settings['category_id'], 123456789012345683)
        self.assertEqual(settings['global_channel_id'], ng.DEFAULT_GLOBAL)
        self.assertEqual(settings['factions'], ng.DEFAULT_FACTIONS)

    def test_shop_setting_persists(self):
        try:
            from modules.player_shop_channels import category_for, set_category
            from modules.business_location_defaults import get_category_id
        except ImportError as exc:
            self.skipTest(f'Discord library not installed: {exc}')
        guild = 123456789012345678
        self.assertIsNone(category_for(guild))
        set_category(guild, 123456789012345684)
        self.assertEqual(category_for(guild), 123456789012345684)
        self.assertEqual(get_category_id(guild), 123456789012345684)


if __name__ == '__main__':
    unittest.main()
