"""直播金流抽成與 7 日頻率風險測試，不需要 Discord Token。"""
import os
import tempfile
import unittest
from unittest.mock import patch

from core import config, database, live_stream, live_social, live_checkout


class LiveIncomeFrequencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = patch.object(config, 'DATABASE_PATH', os.path.join(self.tmp.name, 'test.sqlite3'))
        self.cfg.start()
        self.dbp = patch.object(database, 'DATABASE_PATH', config.DATABASE_PATH)
        self.dbp.start()
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('123','test')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,occupation,status) VALUES('123','11','actor','演員','active')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,occupation,status) VALUES('123','22','influencer','網紅','active')")
            db.execute("INSERT INTO characters(guild_id,owner_id,name,occupation,status) VALUES('123','33','sender','歌手','active')")

    def tearDown(self):
        self.dbp.stop()
        self.cfg.stop()
        self.tmp.cleanup()

    def test_rolling_window_and_exempt_job(self):
        for n in range(6):
            ts = 1000000 + n * 100
            sid = live_stream.start(123, 11, 1, f'cast{n}', 'sales' if n % 2 else 'personal', 'realtime', now=ts)
            live_stream.stop(123, 11, sid, now=ts + 1)
        status = live_stream.frequency_status(123, 1, now=1000600)
        self.assertTrue(status['at_risk'])
        self.assertEqual(status['count_in_7_days'], 6)
        self.assertEqual(len(status['risk_events']), 1)
        self.assertEqual(status['risk_events'][0]['status'], 'pending_effect_rules')
        self.assertEqual(live_stream.frequency_status(123,1,now=1000000+604800+601)['count_in_7_days'], 0)
        for n in range(6):
            ts=1000000+n*100
            sid=live_stream.start(123,22,2,f'influencer{n}','personal','realtime',now=ts)
            live_stream.stop(123,22,sid,now=ts+1)
        status=live_stream.frequency_status(123,2,now=1000600)
        self.assertFalse(status['at_risk'])
        self.assertFalse(status['risk_events'])

    def test_real_gifts_20_percent_and_idempotence(self):
        sid=live_stream.start(123,11,1,'test','personal','realtime',now=100)
        with database.transaction() as db:
            payer=database.get_or_create_account(db,123,'character',3,'wallet')
            reserve=database.get_or_create_account(db,123,'system','test_seed','wallet')
            t=db.execute("INSERT INTO ledger_transactions(guild_id,unique_key,description) VALUES('123','seed-gift','seed')").lastrowid
            db.executemany('INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount) VALUES(?,?,?,?)',[(123,t,reserve,-10000),(123,t,payer,10000)])
        receipt=live_social.gift(123,sid,33,3,'星光','same-request',now=101)
        self.assertEqual(receipt['gross_amount'],1000)
        self.assertEqual(receipt['platform_fee'],200)
        self.assertEqual(receipt['received_amount'],800)
        self.assertEqual(live_social.gift(123,sid,33,3,'星光','same-request',now=102)['id'],receipt['id'])
        with database.get_connection() as db:
            balances={kind: database.account_balance(db,123,database.get_or_create_account(db,123,*identity)) for kind,identity in {
                'sender':('character',3,'wallet'),'host':('character',1,'wallet'),'platform':('system','live_platform','wallet')}.items()}
        self.assertEqual(balances['sender'],9000)
        self.assertEqual(balances['host'],800)
        self.assertEqual(balances['platform'],200)

    def test_commerce_schema_has_fee_and_commission_fields(self):
        with database.transaction() as db:
            live_checkout._init(db)
            columns={r['name'] for r in db.execute('PRAGMA table_info(rp_live_sale_orders)')}
        self.assertIn('platform_fee',columns)
        self.assertIn('commission_gross',columns)

if __name__ == '__main__':
    unittest.main()
