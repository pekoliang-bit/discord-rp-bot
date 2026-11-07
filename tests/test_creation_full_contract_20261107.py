"""創角核准設定、SQLite 原子銜接、角色代理與跨系統回歸；不連接 Discord。"""
from __future__ import annotations
import importlib.util
import json
import re
import sqlite3
import sys
import types
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import character_creation_consent as consent
from core import character_proxy_identity as proxy


class CreationFullContracts(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('''CREATE TABLE characters(guild_id TEXT,id INTEGER,owner_id TEXT,
          name TEXT,status TEXT,settings_json TEXT,public_code TEXT)''')
        self.db.execute("INSERT INTO characters VALUES('10',23,'100','小明','active','{}',NULL)")
        self.db.execute("INSERT INTO characters VALUES('11',23,'200','小玲','active','{}',NULL)")

    def tearDown(self):
        self.db.close()

    def test_default_privacy_and_auth(self):
        state=consent.normalize({})
        self.assertFalse(state['show_personality'])
        self.assertFalse(state['show_biography'])
        self.assertTrue(all(state[name] for name in ('rumor_enabled','network_opinion_enabled','random_event_enabled','official_story_enabled')))
        self.assertEqual(len(state['rumor_categories']),6)
        self.assertTrue(all(state['rumor_categories'].values()))

    def test_categories_independent(self):
        value=consent.update({},'戀情',False)
        self.assertFalse(consent.permitted(value,'rumor','戀情'))
        self.assertTrue(consent.permitted(value,'rumor','工作'))
        self.assertTrue(consent.permitted(consent.update(value,'戀情',True),'rumor','戀情'))

    def test_global_disable_preserves_categories(self):
        value=consent.update({},'戀情',False)
        value=consent.update(value,'rumor_enabled',False)
        self.assertFalse(consent.permitted(value,'rumor','工作'))
        self.assertFalse(consent.permitted(consent.update(value,'rumor_enabled',True),'rumor','戀情'))

    def test_invalid_consent_denied(self):
        with self.assertRaises(ValueError):consent.update({},'other',False)
        with self.assertRaises(ValueError):consent.update({},'rumor_enabled',1)
        with self.assertRaises(ValueError):consent.permitted({},'rumor','未知')

    def test_persist_rumor_separate_guild(self):
        consent.persist_rumor_consent(self.db,10,23,consent.update({},'私生活',False))
        self.assertEqual(self.db.execute('SELECT enabled FROM rp_rumor_category_consent WHERE guild_id=? AND category=?',('10','私生活')).fetchone()[0],0)
        self.assertIsNone(self.db.execute("SELECT * FROM rp_rumor_optout WHERE guild_id='11'").fetchone())
        consent.persist_rumor_consent(self.db,10,23,consent.update({},'rumor_enabled',False))
        self.assertEqual(self.db.execute("SELECT enabled FROM rp_rumor_optout WHERE guild_id='10'").fetchone()[0],0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rp_rumor_category_consent WHERE guild_id='10'").fetchone()[0],6)

    def test_live_eligibility_and_denial(self):
        self.assertTrue(consent.character_allowed(self.db,10,23,'network_opinion_enabled'))
        self.db.execute("UPDATE characters SET settings_json=? WHERE guild_id='10'",(json.dumps({'network_opinion_enabled':False}),))
        self.assertFalse(consent.character_allowed(self.db,10,23,'network_opinion_enabled'))
        self.assertTrue(consent.character_allowed(self.db,11,23,'network_opinion_enabled'))
        self.db.execute("UPDATE characters SET status='deleted' WHERE guild_id='11'")
        self.assertFalse(consent.character_allowed(self.db,11,23,'random_event_enabled'))

    def test_proxy_approve_activate_and_notice_once(self):
        proxy.approve(self.db,10,23)
        self.assertIsNone(proxy.eligible(self.db,10,23,100))
        proxy.activate(self.db,10,23)
        self.assertIsNotNone(proxy.eligible(self.db,10,23,100))
        self.assertIsNone(proxy.eligible(self.db,10,23,200))
        self.assertIsNone(proxy.eligible(self.db,11,23,100))
        self.assertTrue(proxy.claim_notice(self.db,10,23))
        self.assertFalse(proxy.claim_notice(self.db,10,23))
        proxy.unclaim_notice(self.db,10,23)
        self.assertTrue(proxy.claim_notice(self.db,10,23))
        self.db.execute("UPDATE characters SET status='retired' WHERE guild_id='10'")
        self.assertIsNone(proxy.eligible(self.db,10,23,100))

    def test_proxy_display_uses_persisted_photo(self):
        self.db.execute("UPDATE characters SET settings_json=? WHERE guild_id='10'",(json.dumps({'stage_name':'小天','portrait_url':'https://example.org/p.png'}),))
        proxy.activate(self.db,10,23)
        row=proxy.eligible(self.db,10,23,100)
        self.assertEqual(proxy.display(row),('小天','https://example.org/p.png'))

    def test_creation_ui_has_mandatory_separate_tracks(self):
        s=(ROOT/'modules/creation_wizard.py').read_text()
        self.assertIn('class OccupationSelect',s)
        self.assertIn('class ConsentView',s)
        self.assertIn('class SecondarySelect',s)
        self.assertIn('請選擇與主攻不同的副賽道',s)
        self.assertIn('settings_to_store=normalized_consent(saved)',s)

    def test_review_owner_override_requires_real_guild_owner_id(self):
        s=(ROOT/'modules/review_center.py').read_text()
        self.assertIn('guild_owner_id=guild_owner_id',s)
        self.assertIn('and not is_guild_owner',s)
        self.assertIn('register_proxy(conn,actor.guild_id,number)',s)
        s2=(ROOT/'core/permissions.py').read_text()
        self.assertIn('register_proxy(conn,actor.guild_id,character_id)',s2)

    def test_proxy_requires_message_intent(self):
        s=(ROOT/'main.py').read_text()
        self.assertIn('RP_PROXY_MESSAGE_CONTENT_ENABLED',s)
        speaker=(ROOT/'modules/character_proxy_speaker.py').read_text()
        self.assertIn('eligible(db,guild.id,cid,message.author.id)',speaker)
        self.assertIn('discord.AllowedMentions.none()',speaker)
        self.assertIn('message.webhook_id',speaker)

    def test_approved_base_rules_main_secondary(self):
        # Stub core.permissions (the production attribute module itself is loaded verbatim).
        stub=types.ModuleType('core.permissions')
        stub.InvalidState=type('InvalidState',(Exception,),{})
        before=sys.modules.get('core.permissions')
        sys.modules['core.permissions']=stub
        try:
            spec=importlib.util.spec_from_file_location('creation_attr_contract',ROOT/'core/attribute_validation.py')
            m=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            schema={'general':['流量熱度','商業價值','路人緣','公關','影響指數'],
                    'talent':['容貌','才智','歌唱','舞蹈','演繹','主持','綜藝','個人才藝']}
            general={k:0 for k in schema['general']}
            talent={k:0 for k in schema['talent']}
            values=m.calculate_values(schema,'演繹',general,talent,'舞蹈')
            self.assertEqual(values['演繹'],65)
            self.assertEqual(values['舞蹈'],40)
            self.assertEqual(values['影響指數'],20)
            with self.assertRaises(stub.InvalidState):m.calculate_values(schema,'演繹',general,talent,'演繹')
            talent['舞蹈']=126
            with self.assertRaises(stub.InvalidState):m.calculate_values(schema,'演繹',general,talent,'舞蹈')
        finally:
            if before is None:sys.modules.pop('core.permissions',None)
            else:sys.modules['core.permissions']=before


if __name__=='__main__':unittest.main()
