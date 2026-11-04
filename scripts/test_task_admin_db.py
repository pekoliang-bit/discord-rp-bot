"""Offline SQLite tests using temporary DB. Does not touch real player data."""
import unittest, tempfile
from pathlib import Path
from unittest.mock import patch
from modules import task_repository as repo
from modules.task_permissions import grant,has_permission
from modules.task_review import decide,pending

class TaskAdminDB(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        import core.database as database
        self.old=database.get_connection
        # Override core config in module importing DATABASE_PATH.
        self.patch=patch.object(database,'DATABASE_PATH',str(Path(self.tmp.name)/'test.sqlite3'))
        self.patch.start()
        repo.initialize()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def task(self):
        repo.publish_task('guild','work','daily','任務',requirements={},report={'mode':'manual','channel_id':'123'},status='draft')
        import core.database as database
        with database.get_connection() as db:return dict(db.execute('SELECT * FROM rp_task_definitions WHERE code="work"').fetchone())
    def test_version_conflict(self):
        row=self.task();repo.admin_update('guild',row['id'],row['version'],{'title':'新版'})
        with self.assertRaises(ValueError):repo.admin_update('guild',row['id'],row['version'],{'title':'舊版覆蓋'})
    def test_publish(self):
        row=self.task();published=repo.admin_set_status('guild',row['id'],'published',row['version']);self.assertEqual(published['status'],'published')
    def test_permission(self):
        self.assertTrue(has_permission('guild',1,1));self.assertFalse(has_permission('guild',2,1))
        grant('guild',1,1,2,True);self.assertTrue(has_permission('guild',2,1))
        grant('guild',1,1,2,False);self.assertFalse(has_permission('guild',2,1))
    def test_reviewer_does_not_pay(self):
        self.assertEqual(pending('guild'),[])
        with self.assertRaises(ValueError):decide('guild',1,1,4,2,'once',True)
if __name__=='__main__':unittest.main()
