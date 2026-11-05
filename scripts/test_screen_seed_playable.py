"""首批影視作品種子、固定出演保護及席位安全測試（隔離資料庫）。"""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core.database as dbmod
from modules.task_repository import initialize, record_event
from modules.task_newbie import REQUIRED_COMMANDS
from modules.task_screen_seed import seed_screen_tasks
from modules.task_service import board, accept

class ScreenSeedTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=dbmod.DATABASE_PATH
        dbmod.DATABASE_PATH=Path(self.temp.name)/'screen.sqlite3'
        dbmod.init_db();dbmod.register_guild('77','screen')
        initialize()
        with dbmod.transaction() as db:
            db.execute('CREATE TABLE rp_character_status (guild_id TEXT,character_id INTEGER,level INTEGER)')
        self.characters=[]
        for index in range(6):
            uid=str(900+index)
            cid=dbmod.create_character('77',uid,f'測試演員{index}',occupation='演員',age=21)
            self.characters.append((cid,uid))
            with dbmod.transaction() as db:
                db.execute("UPDATE characters SET status='active' WHERE id=?",(cid,))
                db.execute("INSERT INTO rp_character_status VALUES ('77',?,20)",(cid,))
                db.execute("INSERT INTO attributes(guild_id,character_id,name,value) VALUES ('77',?,'演繹',12000)",(cid,))
                db.execute("INSERT INTO attributes(guild_id,character_id,name,value) VALUES ('77',?,'舞蹈',12000)",(cid,))
            for cmd in REQUIRED_COMMANDS:record_event('77',cid,'newbie:'+cmd,'command:'+cmd)
    def tearDown(self):
        dbmod.DATABASE_PATH=self.old;self.temp.cleanup()
    def test_idempotent_publication_and_locked_cast(self):
        self.assertEqual(seed_screen_tasks('77'),43)
        self.assertEqual(seed_screen_tasks('77'),0)
        with dbmod.get_connection() as db:
            self.assertFalse(db.execute("SELECT 1 FROM rp_task_definitions WHERE title LIKE '%沈宇峻%' OR title LIKE '%周潯振%' OR title LIKE '%江馳%'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM rp_task_definitions WHERE level > 3 AND status='published'").fetchone())
    def test_exclusive_and_capacity(self):
        seed_screen_tasks('77')
        c,uid=self.characters[0]
        tasks=board('77',c,uid,'screen')
        featured=next(t for t in tasks if t['title']=='《無聲之夜》特約演員')
        other=next(t for t in tasks if t['title']=='《無聲之夜》女主角：溫茜兒')
        for cid,owner in self.characters[:4]:self.assertTrue(accept('77',cid,owner,featured['id']))
        with self.assertRaisesRegex(ValueError,'額滿'):
            accept('77',*self.characters[4],featured['id'])
        with self.assertRaisesRegex(ValueError,'同一角色'):
            accept('77',c,uid,other['id'])
    def test_newcomer_jobs_and_cooldown(self):
        seed_screen_tasks('77')
        c,uid=self.characters[0]
        job=next(t for t in board('77',c,uid,'screen') if t['code']=='screen_basic_ad_bg')
        self.assertTrue(accept('77',c,uid,job['id']))
        with self.assertRaisesRegex(ValueError,'不能重複'):
            accept('77',c,uid,job['id'])

if __name__=='__main__':unittest.main()
