import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

class ScheduleReminderContract(unittest.TestCase):
    def test_first_panel_has_missed_reminder(self):
        s=(ROOT/'modules/character_view.py').read_text()
        self.assertIn('工作檔期待確認',s)
        self.assertIn('結算倒數',s)
        self.assertIn('missing_registration(actor.guild_id,character_id',s)
    def test_admin_exposes_backfill(self):
        s=(ROOT/'modules/task_admin.py').read_text()
        self.assertIn('class LegacyScheduleModal',s)
        self.assertIn('補登記舊工作',s)
    def test_backfill_atomic(self):
        s=(ROOT/'core/work_schedule.py').read_text()
        self.assertIn('def register_legacy_schedule(',s)
        self.assertIn('with transaction() as db:',s)
        self.assertIn('rp_work_schedule_backfill_audit',s)
    def test_rank_is_not_schedule(self):
        s=(ROOT/'modules/leaderboard_ui.py').read_text()
        self.assertIn('小結算（14天）',s)
        self.assertIn('大結算（30天）',s)

if __name__=='__main__':unittest.main()

class ScheduleBackfillBehavior(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch('core.database.DATABASE_PATH',str(Path(self.temp.name)/'test.sqlite3'))
        self.patch.start()
        from modules.task_repository import initialize
        from core.database import transaction
        initialize()
        with transaction() as db:
            for ident,code,kind in [(100,'legacy_screen','screen'),(101,'separate_variety','variety')]:
                db.execute("INSERT INTO rp_task_definitions(id,guild_id,code,kind,title,status,level) VALUES(?,?,?,?,?,'published',1)",
                    (ident,'g',code,kind,code))
            for character in (1,2):
                db.execute("INSERT INTO rp_task_progress(guild_id,character_id,task_id,cycle_key,status,definition_version,snapshot_json) VALUES(?,?,?,?,?,?,?)",
                    ('g',character,100,'once','accepted',1,'{}'))
        from datetime import datetime,timezone,timedelta
        self.begin=datetime.now(timezone.utc)+timedelta(days=7)

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def span(self,start,end):
        from datetime import timedelta
        return (self.begin+timedelta(hours=start)).isoformat(),(self.begin+timedelta(hours=end)).isoformat()

    def test_real_legacy_booking_and_idempotence(self):
        from core.work_schedule import missing_registration,register_legacy_schedule,upcoming
        self.assertEqual(len(missing_registration('g',1)),1)
        start,end=self.span(0,10)
        self.assertEqual(register_legacy_schedule('g',100,start,end,'admin'),2)
        self.assertEqual(register_legacy_schedule('g',100,start,end,'admin'),0)
        self.assertEqual(missing_registration('g',1),[])
        self.assertEqual(len(upcoming('g',1)),1)
        self.assertEqual(len(upcoming('g',2)),1)

    def test_conflict_rolls_back_all_users(self):
        from core.work_schedule import set_task_schedule,register_legacy_schedule,upcoming,missing_registration
        from modules.task_repository import accept_task
        start,end=self.span(5,15)
        set_task_schedule('g',101,start,end,'admin')
        self.assertTrue(accept_task('g',2,101))
        with self.assertRaisesRegex(ValueError,'撞期'):
            register_legacy_schedule('g',100,*self.span(0,10),'admin')
        self.assertEqual(len(upcoming('g',1)),0)
        self.assertEqual(len(missing_registration('g',1)),1)

    def test_no_backfill_without_existing_acceptance(self):
        from core.work_schedule import register_legacy_schedule
        with self.assertRaisesRegex(ValueError,'找不到待補登記'):
            register_legacy_schedule('g',101,*self.span(0,10),'admin')
