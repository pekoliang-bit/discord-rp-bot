"""額外資產整合測試；以全新暫存資料庫執行，不讀正式玩家資料。"""
import os
import tempfile
import unittest
from pathlib import Path

class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['DATABASE_PATH']=str(Path(cls.tmp.name)/'completion.sqlite3')
        from core.database import init_db,transaction,get_or_create_account
        from core.economy import _payment
        from core import large_assets as a, asset_completions as c
        cls.a=a;cls.c=c
        init_db();a.init_assets()
        with transaction() as db:
            c.initialize(db)
            db.execute("INSERT INTO guilds(guild_id) VALUES('gt')")
            funding=get_or_create_account(db,'gt','system','test_funding','wallet')
            for cid,user in ((201,'u201'),(202,'u202'),(203,'u203')):
                db.execute('INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(?,?,?,?,?)',(cid,'gt',user,f'Test {cid}','active'))
                account=get_or_create_account(db,'gt','character',str(cid),'wallet')
                _payment(db,'gt',f'fund:{cid}',funding,account,10**8,'測試注資')
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_01_share_consent_idempotent(self):
        a=self.a;c=self.c
        asset=a.buy('gt','CAR-06',201,'u201')
        invitation=c.request_share('gt',asset,201,202,2500,'u201')
        with self.assertRaises(a.AssetError):c.accept_share('gt',invitation,203,'u203')
        self.assertEqual(c.accept_share('gt',invitation,202,'u202'),asset)
        with self.assertRaises(a.AssetError):c.accept_share('gt',invitation,202,'u202')
        shares={h['basis_points'] for h in a.holdings('gt',201) if h['id']==asset}
        self.assertIn(7500,shares)
        type(self).shared_asset=asset

    def test_02_shared_maintenance(self):
        a=self.a;c=self.c
        with a.transaction() as db:
            db.execute("UPDATE rp_asset_items SET created_at=created_at-2678400 WHERE id=?",(self.shared_asset,))
        a.generate_maintenance('gt')
        a.apply_depreciation('gt')
        self.assertGreater(c.pay_shared_maintenance('gt',self.shared_asset,'character',201,'u201'),0)
        self.assertGreater(c.pay_shared_maintenance('gt',self.shared_asset,'character',202,'u202'),0)
        self.assertEqual(a.get_unpaid_maintenance('gt',self.shared_asset)['due'],0)
        with self.assertRaises(a.AssetError):c.pay_shared_maintenance('gt',self.shared_asset,'character',201,'u201')

    def test_03_official_renew(self):
        a=self.a;c=self.c
        asset,lease=a.rent_official('gt','EST-01',201,'u201',1)
        with a.get_connection() as db:
            end=db.execute('SELECT ends_at FROM rp_asset_leases WHERE id=?',(lease,)).fetchone()['ends_at']
        self.assertEqual(c.renew_lease('gt',lease,201,'u201',2),end+14*86400)
        with self.assertRaises(a.AssetError):c.renew_lease('gt',lease,202,'u202',1)

    def test_04_early_cancel_consent(self):
        a=self.a;c=self.c
        home=a.buy('gt','EST-03',201,'u201')
        order=a.offer('gt',home,'character',201,'u201','rent',3500)
        lease=a.lease('gt',home,202,'u202',2,order)
        c.request_early_termination('gt',lease,202,'u202')
        with self.assertRaises(a.AssetError):c.approve_early_termination('gt',lease,203,'u203')
        self.assertGreaterEqual(c.approve_early_termination('gt',lease,201,'u201'),0)
        with self.assertRaises(a.AssetError):c.approve_early_termination('gt',lease,201,'u201')

    def test_045_player_renewal(self):
        a=self.a;c=self.c
        home=a.buy('gt','EST-04',201,'u201')
        offer=a.offer('gt',home,'character',201,'u201','rent',6000)
        lease=a.lease('gt',home,202,'u202',1,offer)
        owner=c.request_player_renewal('gt',lease,202,'u202',2)
        self.assertEqual(owner,201)
        with self.assertRaises(a.AssetError):c.approve_player_renewal('gt',lease,203,'u203')
        new_end=c.approve_player_renewal('gt',lease,201,'u201')
        self.assertGreater(new_end,0)
        with self.assertRaises(a.AssetError):c.approve_player_renewal('gt',lease,201,'u201')

    def test_046_guild_isolation(self):
        a=self.a;c=self.c
        c.update_template('gt','CAR-02','gm',name='本服特殊車',base_price=999999)
        gt=[x for x in a.catalog(guild='gt') if x['code']=='CAR-02'][0]
        other=[x for x in a.catalog(guild='another') if x['code']=='CAR-02'][0]
        self.assertEqual(gt['base_price'],999999)
        self.assertEqual(other['base_price'],30000)
        self.assertNotEqual(gt['name'],other['name'])

    def test_047_manual_appraisal_limits(self):
        a=self.a;c=self.c
        asset=a.buy('gt','EST-02',201,'u201')
        with a.transaction() as db:
            db.execute('UPDATE rp_asset_items SET last_value_at=last_value_at-2678400 WHERE id=?',(asset,))
        old,new=c.appraise_asset('gt',asset,105000,'gm')
        self.assertEqual((old,new),(100000,105000))
        with self.assertRaises(a.AssetError):c.appraise_asset('gt',asset,110000,'gm')

    def test_05_admin_template_and_mortgage(self):
        a=self.a;c=self.c
        code=c.create_template('gt','CAR-98','車輛','測試豪車',500000,True)
        self.assertEqual(code,'CAR-98')
        with self.assertRaises(a.AssetError):c.create_template('gt','CAR-98','車輛','重複',1)
        c.update_template('gt','CAR-98','gm',name='新名字',base_price=650000)
        item=a.buy('gt','CAR-98',201,'u201')
        self.assertEqual(c.mortgage_asset_id('資產抵押#'+str(item)+' 購置資金'),item)
        with a.transaction() as db:
            with self.assertRaises(a.AssetError):c.validate_mortgage(db,'gt',201,item,999999)
            c.bind_bank_loan(db,'gt',909,201,item,100000)
            c.release_bank_loan(db,'gt',909)
        with a.get_connection() as db:
            row=db.execute('SELECT status FROM rp_asset_lien WHERE asset_id=? AND bank_reference=?',(item,'loan:909')).fetchone()
            self.assertEqual(row['status'],'cleared')

if __name__=='__main__':unittest.main()
