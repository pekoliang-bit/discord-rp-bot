"""綜藝首批可玩流程的隔離資料庫回歸驗證。"""
import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core.database as dbmod
from modules.task_repository import initialize,record_event
from modules.task_newbie import REQUIRED_COMMANDS
from modules.task_variety_seed import seed_variety_tasks,publish_episode
from modules.task_variety_cast import pending,decide
from modules.task_service import board,accept

class VarietyPlayableTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=dbmod.DATABASE_PATH
        dbmod.DATABASE_PATH=Path(self.tmp.name)/'variety.sqlite3'
        dbmod.init_db();dbmod.register_guild('88','variety');initialize()
        with dbmod.transaction() as db:
            db.execute('CREATE TABLE rp_character_status (guild_id TEXT,character_id INTEGER,level INTEGER)')
        self.people=[]
        for i in range(10):
            owner=str(800+i)
            cid=dbmod.create_character('88',owner,f'測試藝人{i}',occupation='演員',age=21)
            with dbmod.transaction() as db:
                db.execute("UPDATE characters SET status='active' WHERE id=?",(cid,))
                db.execute('INSERT INTO rp_character_status VALUES (?,?,?)',('88',cid,20))
                for skill in ('綜藝','主持','才智','歌唱','公關'):
                    db.execute('INSERT INTO attributes(guild_id,character_id,name,value) VALUES (?,?,?,?)',('88',cid,skill,8000))
            for cmd in REQUIRED_COMMANDS:record_event('88',cid,'newbie:'+cmd,'command:'+cmd)
            self.people.append((cid,owner))
    def tearDown(self):
        dbmod.DATABASE_PATH=self.old;self.tmp.cleanup()
    def task(self,code):
        with dbmod.get_connection() as db:return dict(db.execute('SELECT * FROM rp_task_definitions WHERE guild_id=? AND code=?',('88',code)).fetchone())
    def test_seed_preserves_all_nine(self):
        self.assertGreater(seed_variety_tasks('88'),0)
        self.assertEqual(seed_variety_tasks('88'),0)
        with dbmod.get_connection() as db:
            self.assertEqual(db.execute('SELECT count(*) x FROM rp_variety_shows WHERE guild_id=?',('88',)).fetchone()['x'],9)
            self.assertEqual(db.execute('SELECT count(*) x FROM rp_variety_fixed_cast WHERE show_key="survival"',()).fetchone()['x'],4)
            self.assertIsNone(db.execute('SELECT 1 FROM rp_task_definitions WHERE code LIKE "variety_street_apply%"').fetchone())
    def test_permanent_application_approval_and_per_episode(self):
        seed_variety_tasks('88')
        cid,uid=self.people[0]
        app=self.task('variety_survival_apply_resident')
        self.assertIn(app,[*board('88',cid,uid,'variety')])
        self.assertTrue(str(accept('88',cid,uid,app['id'])).startswith('applied:'))
        self.assertEqual(len(pending('88')),1)
        with self.assertRaisesRegex(ValueError,'待審'):accept('88',cid,uid,app['id'])
        with self.assertRaisesRegex(ValueError,'已滿'):
            # 插入第五個已預約席位後，再嘗試批准測試角色
            with dbmod.transaction() as db:
                db.execute("UPDATE rp_variety_seats SET reserved_count=5 WHERE guild_id='88' AND show_key='survival' AND seat='resident'")
            decide('88',pending('88')[0]['id'],'1','1',True)
        with dbmod.transaction() as db:
            db.execute("UPDATE rp_variety_seats SET reserved_count=4 WHERE guild_id='88' AND show_key='survival' AND seat='resident'")
        decide('88',pending('88')[0]['id'],'1','1',True)
        task=self.task('variety_survival_e1_resident')
        self.assertIn(task,[*board('88',cid,uid,'variety')])
        self.assertTrue(accept('88',cid,uid,task['id']))
        self.assertEqual(publish_episode('88','survival',2),1)
        self.assertEqual(publish_episode('88','survival',2),0)
        self.assertTrue(accept('88',cid,uid,self.task('variety_survival_e2_resident')['id']))
    def test_broadcast_once_only(self):
        from modules.task_variety_cast import broadcast
        seed_variety_tasks('88')
        cid,owner=self.people[0]
        t=self.task('variety_cook_e1_flight')
        self.assertTrue(accept('88',cid,owner,t['id']))
        with dbmod.transaction() as db:
            db.execute("UPDATE rp_task_progress SET status='claimed' WHERE guild_id='88' AND character_id=? AND task_id=?",(cid,t['id']))
        self.assertEqual(broadcast('88','cook',1,'1','1',1.5),1)
        self.assertEqual(broadcast('88','cook',1,'1','1',1.5),1)
        with dbmod.get_connection() as db:
            self.assertEqual(db.execute("SELECT count(*) c FROM rp_stat_reward_log WHERE guild_id='88' AND character_id=? AND source_id LIKE 'variety:air:%'",(cid,)).fetchone()['c'],1)
            awareness=db.execute("SELECT value FROM rp_reward_metrics WHERE guild_id='88' AND character_id=? AND metric='大眾認知度'",(cid,)).fetchone()['value']
            self.assertEqual(awareness,90)
    def test_flight_capacity_and_episode_exclusivity(self):
        seed_variety_tasks('88')
        tid=self.task('variety_cook_e1_flight')['id']
        for cid,owner in self.people[:4]:self.assertTrue(accept('88',cid,owner,tid))
        with self.assertRaisesRegex(ValueError,'額滿'):
            accept('88',*self.people[4],tid)
        host_app=self.task('variety_cook_apply_host')
        cid,owner=self.people[0]
        self.assertTrue(str(accept('88',cid,owner,host_app['id'])).startswith('applied:'))
        decide('88',pending('88')[0]['id'],'1','1',True)
        with self.assertRaisesRegex(ValueError,'同一角色'):
            accept('88',cid,owner,self.task('variety_cook_e1_host')['id'])

if __name__=='__main__':unittest.main()
