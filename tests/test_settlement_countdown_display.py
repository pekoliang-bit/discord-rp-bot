import unittest
from datetime import datetime,timezone,timedelta
from modules.leaderboard_cycle import settlement_countdown,next_period,remaining_text

class SettlementCountdownTests(unittest.TestCase):
    def test_fourteen_thirty_are_independent(self):
        anchor=datetime(2026,11,1,tzinfo=timezone.utc)
        now=anchor+timedelta(days=15,hours=3)
        value=settlement_countdown(anchor,now)
        self.assertEqual(value['14d']['index'],2)
        self.assertEqual(value['30d']['index'],1)
        self.assertEqual(value['14d']['remaining'],'12天21小時0分鐘')
        self.assertEqual(value['30d']['remaining'],'14天21小時0分鐘')
    def test_before_start(self):
        anchor=datetime(2026,11,10,tzinfo=timezone.utc)
        now=anchor-timedelta(days=2)
        self.assertEqual(next_period(anchor,now,14)[0],1)
    def test_zero_seconds_clamped(self):
        now=datetime(2026,11,1,tzinfo=timezone.utc)
        self.assertEqual(remaining_text(now-timedelta(hours=1),now),'0天0小時0分鐘')
    def test_timezones_required(self):
        with self.assertRaises(ValueError):
            next_period(datetime(2026,11,1),datetime.now(timezone.utc),14)

if __name__=='__main__':unittest.main()
