"""結婚／離婚要求雙方玩家各自提交互相對應的申請。"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from core import database
from core.general_case_executors import initialize_general_cases,execute_general_case

class MarriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=patch.object(database,'DATABASE_PATH',Path(self.tmp.name)/'m.db');p.start();self.addCleanup(p.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES('333')")
            for uid,name in [('a','小明'),('b','小華')]:
                db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('333',?,?,'active')",(uid,name))
            rows=db.execute("SELECT id,name FROM characters ORDER BY id").fetchall()
            self.a,self.b=[int(r['id']) for r in rows]
            initialize_general_cases(db)
            db.execute('''CREATE TABLE IF NOT EXISTS rp_general_applications(
                id INTEGER PRIMARY KEY, guild_id TEXT,user_id TEXT,character_id INTEGER,
                kind TEXT,title TEXT,details TEXT,status TEXT,reviewer_id TEXT,reviewed_at TEXT)''')

    def _append(self, db, cid, uid, target_id, target_name, action):
        details=f'【對方角色姓名】{target_name}\n【對方角色編號】{target_id}\n【登記操作（結婚／離婚）】{action}'
        cur=db.execute("INSERT INTO rp_general_applications(guild_id,user_id,character_id,kind,title,details,status) "
            "VALUES('333',?,?,?,?,?,'pending')",(uid,cid,'cp_register',target_name,details))
        return dict(db.execute('SELECT * FROM rp_general_applications WHERE id=?',(cur.lastrowid,)).fetchone())

    def test_two_person_marriage_divorce(self):
        with database.transaction() as db:
            x=self._append(db,self.a,'a',self.b,'小華','結婚')
        with self.assertRaises(ValueError):
            with database.transaction() as db:
                execute_general_case(db,x,'reviewer')
        with database.transaction() as db:
            self._append(db,self.b,'b',self.a,'小明','結婚')
            execute_general_case(db,x,'reviewer')
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rp_registered_partnerships').fetchone()[0],1)
        with database.transaction() as db:
            y=self._append(db,self.a,'a',self.b,'小華','離婚')
            self._append(db,self.b,'b',self.a,'小明','離婚')
            execute_general_case(db,y,'reviewer')
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rp_registered_partnerships').fetchone()[0],0)

if __name__=='__main__':unittest.main()
