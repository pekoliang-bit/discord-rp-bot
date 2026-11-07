"""在臨時 SQLite 資料庫驗證創角→配點→雙審→啟用及授權同步。"""
from __future__ import annotations
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

# This subprocess has a separate module namespace; use only a minimal Discord stub
# if discord.py is not available in the local offline test environment.
if importlib.util.find_spec('discord') is None:
    D=types.ModuleType('discord');D.Interaction=object
    ext=types.ModuleType('discord.ext');cmd=types.ModuleType('discord.ext.commands')
    cmd.Cog=type('Cog',(),{});cmd.Bot=type('Bot',(),{});ext.commands=cmd;D.ext=ext
    sys.modules.update({'discord':D,'discord.ext':ext,'discord.ext.commands':cmd})

from core import database as db
from core.permissions import Actor,PermissionDenied,submit_character,review_character
from modules import attributes as attr
from core.attribute_validation import validate_initial_attributes
from core.character_creation_consent import persist_rumor_consent,normalize
from core.character_proxy_identity import activate,eligible


class CreationDbIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_path=db.DATABASE_PATH
        db.DATABASE_PATH=str(Path(self.tmp.name)/'role.db')
        db.init_db()
        self.cid=db.create_character('123','100','小明',25,'導演')
        schema=attr.default_schema()
        general={key:0 for key in schema['general']}
        talent={key:0 for key in schema['talent']}
        self.alloc=attr.allocate_initial(Actor('123','100'),self.cid,'演繹','舞蹈',general,talent)
        with db.transaction() as conn:
            settings=json.loads(conn.execute('SELECT settings_json FROM characters WHERE id=?',(self.cid,)).fetchone()[0])
            self.settings={**settings,**normalize({'show_biography':True,'rumor_categories':{'戀情':False}})}
            conn.execute('UPDATE characters SET settings_json=? WHERE id=?',(json.dumps(self.settings,ensure_ascii=False),self.cid))

    def tearDown(self):
        db.DATABASE_PATH=self.old_path
        self.tmp.cleanup()

    def test_initial_tracks_and_restricted_stat(self):
        with db.get_connection() as conn:
            data=validate_initial_attributes(conn,'123',self.cid)
        self.assertEqual(data['main_track'],'演繹')
        self.assertEqual(data['secondary_track'],'舞蹈')
        self.assertEqual(data['attributes']['影響指數'],20)
        self.assertEqual(data['attributes']['演繹'],65)
        self.assertEqual(data['attributes']['舞蹈'],40)

    def test_owner_review_activation_and_rumor_sync(self):
        self.assertEqual(submit_character(Actor('123','100'),self.cid),'待第一階段審核')
        reviewer=Actor('123','100',True)
        self.assertIn('第二審',review_character(reviewer,self.cid,approve=True,guild_owner_id=100))
        self.assertIn('啟用',review_character(reviewer,self.cid,approve=True,guild_owner_id=100))
        with db.transaction() as conn:
            state=conn.execute('SELECT state FROM rp_character_proxy_identity WHERE guild_id=? AND character_id=?',('123',self.cid)).fetchone()
            self.assertEqual(state['state'],'approved')
            conn.execute("UPDATE characters SET status='active' WHERE guild_id=? AND id=?",('123',self.cid))
            persist_rumor_consent(conn,123,self.cid,self.settings)
            activate(conn,123,self.cid)
        with db.get_connection() as conn:
            self.assertIsNotNone(eligible(conn,123,self.cid,100))
            self.assertIsNone(eligible(conn,123,self.cid,999))
            self.assertEqual(conn.execute("SELECT enabled FROM rp_rumor_optout WHERE guild_id='123'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT enabled FROM rp_rumor_category_consent WHERE guild_id='123' AND category='戀情'").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT enabled FROM rp_rumor_category_consent WHERE guild_id='123' AND category='工作'").fetchone()[0],1)

    def test_nonowner_cannot_review_own_character(self):
        submit_character(Actor('123','100'),self.cid)
        with self.assertRaises(PermissionDenied):
            review_character(Actor('123','100',True),self.cid,approve=True)

    def test_two_non_owner_reviewer_must_differ(self):
        submit_character(Actor('123','100'),self.cid)
        reviewer=Actor('123','222',True)
        review_character(reviewer,self.cid,approve=True)
        with self.assertRaises(PermissionDenied):
            review_character(reviewer,self.cid,approve=True)
        self.assertIn('啟用',review_character(Actor('123','333',True),self.cid,approve=True))


if __name__=='__main__':unittest.main()
