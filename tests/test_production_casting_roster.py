"""第五部分：導演報名者名單的權限與名額統計。"""
import sqlite3
import unittest
from core.production_casting_audit import count_applicants, list_applicants


class CastingRosterTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE characters (guild_id TEXT, id INTEGER, name TEXT, owner_id TEXT,
                    status TEXT, character_type TEXT);
          CREATE TABLE productions (guild_id TEXT, id INTEGER, owner_character_id INTEGER);
          CREATE TABLE production_positions (guild_id TEXT,id INTEGER,production_id INTEGER,
                    title TEXT,capacity INTEGER);
          CREATE TABLE production_applications (guild_id TEXT,id INTEGER,production_id INTEGER,
                    position_id INTEGER,character_id INTEGER,status TEXT);
          INSERT INTO characters VALUES ('g',1,'導演','owner','active','player');
          INSERT INTO characters VALUES ('g',2,'演員甲','actor-a','active','player');
          INSERT INTO characters VALUES ('g',3,'演員乙','actor-b','active','player');
          INSERT INTO productions VALUES ('g',10,1);
          INSERT INTO production_positions VALUES ('g',50,10,'主演',1);
          INSERT INTO production_applications VALUES ('g',100,10,50,2,'accepted');
          INSERT INTO production_applications VALUES ('g',101,10,50,3,'pending');
        ''')

    def tearDown(self):
        self.db.close()

    def test_owner_can_view(self):
        self.assertEqual(count_applicants(self.db,'g',10,'owner'),2)
        rows=list_applicants(self.db,'g',10,'owner')
        self.assertEqual([r['status'] for r in rows],['pending','accepted'])
        self.assertEqual([r['filled'] for r in rows],[1,1])
        self.assertEqual(rows[0]['position_title'],'主演')

    def test_other_user_and_guild_forbidden(self):
        for guild,user in [('g','actor-a'),('other','owner')]:
            with self.assertRaises(PermissionError):
                list_applicants(self.db,guild,10,user)

    def test_pagination(self):
        self.assertEqual(len(list_applicants(self.db,'g',10,'owner',limit=1)),1)
        self.assertEqual(list_applicants(self.db,'g',10,'owner',offset=1,limit=1)[0]['id'],100)

    def test_director_disabled(self):
        self.db.execute("UPDATE characters SET status='inactive' WHERE id=1")
        with self.assertRaises(PermissionError):
            count_applicants(self.db,'g',10,'owner')


if __name__=='__main__':
    unittest.main()
