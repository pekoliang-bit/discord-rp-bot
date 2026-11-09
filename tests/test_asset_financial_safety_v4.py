
"""第四部分新增防回歸測試；在獨立 SQLite 中執行，不連 Discord。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class FourthPartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        from core import database
        cls._original_db_path = database.DATABASE_PATH
        database.DATABASE_PATH = str(Path(cls.temp.name) / 'fourth.sqlite3')
        from core.database import init_db, transaction, get_or_create_account
        from core.economy import _payment
        from core import large_assets as a
        from core import asset_completions as x
        from core import asset_integrity as integrity
        cls.a, cls.x, cls.integrity = a, x, integrity
        init_db()
        a.init_assets()
        with transaction() as db:
            db.execute("INSERT INTO guilds(guild_id) VALUES('v4')")
            for cid in (701,702):
                db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(?,?,?,?,?)",
                    (cid,'v4','owner'+str(cid),'Role '+str(cid),'active'))
                src = get_or_create_account(db,'v4','system','v4-funding','wallet')
                dest = get_or_create_account(db,'v4','character',str(cid),'wallet')
                _payment(db,'v4',f'v4:fund:{cid}',src,dest,10**9,'測試資金')

    @classmethod
    def tearDownClass(cls):
        from core import database
        database.DATABASE_PATH = cls._original_db_path
        cls.temp.cleanup()

    def test_01_loan_partial_and_full(self):
        a,x=self.a,self.x
        asset=a.buy('v4','CAR-06',701,'owner701')
        with a.transaction() as db:
            x.bind_bank_loan(db,'v4',3001,701,asset,100000)
            self.assertEqual(x.update_bank_loan_balance(db,'v4',3001,100000,110000,20000),90000)
            row=db.execute("SELECT outstanding_amount,status FROM rp_asset_lien WHERE guild_id='v4' AND asset_id=?",(asset,)).fetchone()
            self.assertEqual((row['outstanding_amount'],row['status']),(90000,'active'))
            self.assertEqual(x.update_bank_loan_balance(db,'v4',3001,100000,110000,110000),0)
        with a.get_connection() as db:
            row=db.execute("SELECT status FROM rp_asset_lien WHERE guild_id='v4' AND asset_id=?",(asset,)).fetchone()
        self.assertEqual(row['status'],'cleared')
        self.assertTrue(x.validate_liquidation('v4','character',702))

    def test_02_nocreate_unsecured_lien(self):
        a,x=self.a,self.x
        with a.transaction() as db:
            self.assertEqual(x.update_bank_loan_balance(db,'v4',9999,100000,110000,5000),100000)
            self.assertIsNone(db.execute("SELECT 1 FROM rp_asset_lien WHERE bank_reference='loan:9999'").fetchone())

    def test_03_reject_invalid_balance(self):
        a,x=self.a,self.x
        with a.transaction() as db:
            for values in ((0,100,20),(100,90,20),(100,110,111)):
                with self.assertRaises(a.AssetError):
                    x.update_bank_loan_balance(db,'v4',3001,*values)

    def test_04_block_share_transfer_with_lease(self):
        a,x=self.a,self.x
        home=a.buy('v4','EST-03',701,'owner701')
        order=a.offer('v4',home,'character',701,'owner701','rent',3500)
        a.lease('v4',home,702,'owner702',1,order)
        with self.assertRaises(a.AssetError):
            x.request_share('v4',home,701,702,1000,'owner701')
        with self.assertRaises(a.AssetError):
            a.transfer_share('v4',home,'character',701,'character',702,1000,'owner701',recipient_accepted=True)

    def test_05_block_share_transfer_with_unpaid_fees(self):
        a,x=self.a,self.x
        fancy=a.buy('v4','CAR-06',701,'owner701')
        with a.transaction() as db:
            db.execute("INSERT INTO rp_asset_dues(guild_id,asset_id,period_start,due_amount,created_at) VALUES('v4',?,?,?,?)",(fancy,100,800,100))
        with self.assertRaises(a.AssetError):
            x.request_share('v4',fancy,701,702,1000,'owner701')
        with self.assertRaises(a.AssetError):
            a.transfer_share('v4',fancy,'character',701,'character',702,1000,'owner701',recipient_accepted=True)

    def test_06_report_restricts_other_owner(self):
        with self.assertRaises(self.a.AssetError):
            self.integrity.report_for_character('v4',701,'owner702')

    def test_07_report_and_liquidation_block(self):
        a=self.a
        result=self.integrity.report_for_character('v4',701,'owner701')
        self.assertFalse(result['ready_for_asset_exit'])
        self.assertTrue(result['shares'])
        self.assertGreater(result['active_landlord_leases'],0)
        self.assertGreater(result['unpaid_maintenance'],0)
        with self.assertRaises(a.AssetError):
            self.x.validate_liquidation('v4','character',701)

    def test_08_report_isolation(self):
        with self.a.transaction() as db:
            db.execute("INSERT OR IGNORE INTO guilds(guild_id) VALUES('other')")
            result=self.integrity.report_in_transaction(db,'other','character',701)
        self.assertEqual(result['shares'],[])
        self.assertTrue(result['ready_for_asset_exit'])


if __name__=='__main__':
    unittest.main()
