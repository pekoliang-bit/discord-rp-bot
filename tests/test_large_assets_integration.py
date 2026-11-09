
"""可直接執行 python -m unittest tests/test_large_assets_integration.py（不需要 Discord 連線）。"""
import os,sys,tempfile,unittest,time
from pathlib import Path
TMP=tempfile.TemporaryDirectory()
from core.database import init_db,transaction,get_or_create_account,account_balance
from core.economy import _payment
from core import large_assets as a

class AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core import database
        cls._original_db_path = database.DATABASE_PATH
        database.DATABASE_PATH = str(Path(TMP.name) / 'asset-test.sqlite3')
        init_db();a.init_assets()
        with transaction() as db:
            db.execute("INSERT INTO guilds(guild_id) VALUES('g1')")
            for cid,uid in ((101,'userA'),(102,'userB'),(103,'userC')):
                db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(?,?,?,?,?)",(cid,'g1',uid,'角色'+str(cid),'active'))
                source=get_or_create_account(db,'g1','system','test_funding','wallet')
                target=get_or_create_account(db,'g1','character',str(cid),'wallet')
                _payment(db,'g1','test:fund:'+str(cid),source,target,1000000000,'測試注資')
    @classmethod
    def tearDownClass(cls):
        from core import database
        database.DATABASE_PATH = cls._original_db_path
        TMP.cleanup()

    def test_1_buy_payment_and_uniqueness(self):
        before=self.balance(101)
        asset=a.buy('g1','EST-01',101,'userA','臺北某公寓 3F-1','test:unique:unit1')
        self.assertEqual(before-self.balance(101),50000)
        self.assertEqual(a.holdings('g1',101)[0]['id'],asset)
        with self.assertRaises(a.AssetError):a.buy('g1','EST-01',102,'userB','同一房號','test:unique:unit1')
        self.asset=asset
    def test_2_resale_atomic(self):
        asset=a.buy('g1','CAR-02',101,'userA')
        order=a.offer('g1',asset,'character',101,'userA','sale',100000)
        before=self.balance(102)
        a.accept('g1',order,102,'userB')
        self.assertEqual(before-self.balance(102),100000)
        self.assertEqual(a.holdings('g1',102)[0]['id'],asset)
        with self.assertRaises(a.AssetError):a.accept('g1',order,103,'userC')
    def test_3_gift_and_elder_unique(self):
        asset,new=a.grant_elder_home('g1',101,'admin',verified=True)
        self.assertTrue(new)
        self.assertEqual(a.grant_elder_home('g1',101,'admin',verified=True),(asset,False))
        order=a.offer('g1',asset,'character',101,'userA','gift',target=102)
        with self.assertRaises(a.AssetError):a.accept('g1',order,103,'userC')
        a.accept('g1',order,102,'userB')
        self.assertEqual(a.grant_elder_home('g1',101,'admin',verified=True),(asset,False))
    def test_4_rent_deposit_and_lease(self):
        asset=a.buy('g1','EST-03',101,'userA')
        order=a.offer('g1',asset,'character',101,'userA','rent',3500)
        money=self.balance(102)
        lease=a.lease('g1',asset,102,'userB',2,order)
        self.assertEqual(money-self.balance(102),10500)
        with self.assertRaises(a.AssetError):a.lease('g1',asset,103,'userC',1,order)
        a.residence('g1',102,asset,'userB')
        # arrival at end time: no early termination without consent
        with self.assertRaises(a.AssetError):a.close_lease('g1',lease)
    def test_5_auction_escrow_and_refund(self):
        asset=a.buy('g1','CAR-04',101,'userA')
        order=a.offer('g1',asset,'character',101,'userA','auction',100000,24)
        before=self.balance(102)
        a.bid('g1',order,102,'userB',100000)
        self.assertEqual(before-self.balance(102),100000)
        a.bid('g1',order,103,'userC',102000)
        self.assertEqual(before,self.balance(102))
        with transaction() as db:db.execute('UPDATE rp_asset_orders SET ends_at=? WHERE guild_id=? AND id=?',(int(time.time())-1,'g1',order))
        a.settle_auction('g1',order)
        self.assertTrue(any(x['id']==asset for x in a.holdings('g1',103)))
        with self.assertRaises(a.AssetError):a.settle_auction('g1',order)
    def test_6_luxury_only_maintenance_and_depreciation(self):
        ordinary=a.buy('g1','CAR-03',101,'userA'); fancy=a.buy('g1','CAR-06',101,'userA')
        with transaction() as db:
            db.execute('UPDATE rp_asset_items SET created_at=created_at-2678400,last_value_at=last_value_at-2678400 WHERE guild_id=? AND id IN (?,?)',('g1',ordinary,fancy))
        a.generate_maintenance('g1')
        with a.get_connection() as db:
            self.assertIsNone(db.execute('SELECT 1 FROM rp_asset_dues WHERE asset_id=?',(ordinary,)).fetchone())
            self.assertIsNotNone(db.execute('SELECT 1 FROM rp_asset_dues WHERE asset_id=?',(fancy,)).fetchone())
        a.pay_maintenance('g1',fancy,101,'userA')
        a.apply_depreciation('g1')
        with a.get_connection() as db:
            value=db.execute('SELECT appraised_value FROM rp_asset_items WHERE id=?',(ordinary,)).fetchone()['appraised_value']
        self.assertLess(value,60000)
    def test_7_official_rental_no_ownership(self):
        before=self.balance(102)
        unit,lease=a.rent_official('g1','EST-01',102,'userB',1)
        self.assertEqual(self.balance(102),before-2000)
        self.assertFalse(any(x['id']==unit for x in a.holdings('g1',102)))
        a.residence('g1',102,unit,'userB')
        with transaction() as db:
            db.execute('UPDATE rp_asset_leases SET ends_at=? WHERE guild_id=? AND id=?',(int(time.time())-1,'g1',lease))
        self.assertEqual(a.close_expired_leases('g1'),1)
        self.assertEqual(self.balance(102),before-1000)
    def test_8_company_treasury_purchase(self):
        with transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS rp_business_entities (
               guild_id TEXT,business_id TEXT,kind TEXT,name TEXT,category TEXT,
               industry TEXT,description TEXT,region TEXT,owner_id TEXT,
               owner_character_id INTEGER,parent_id TEXT,representative_character_id INTEGER,
               brand_tier TEXT,status TEXT,PRIMARY KEY(guild_id,business_id))""")
            db.execute("CREATE TABLE IF NOT EXISTS rp_business_managers(guild_id TEXT,business_id TEXT,user_id TEXT,character_id INTEGER,permissions TEXT,status TEXT)")
            db.execute("INSERT INTO rp_business_entities(guild_id,business_id,owner_id,status) VALUES('g1','c1','userA','active')")
            source=get_or_create_account(db,'g1','system','test_funding','wallet')
            target=get_or_create_account(db,'g1','organization','c1','treasury')
            _payment(db,'g1','test:company:fund',source,target,5000000,'測試公司資金')
        asset=a.buy_for_company('g1','CAR-05','c1','userA')
        self.assertTrue(any(x['id']==asset for x in a.holdings('g1','c1','organization')))
        with self.assertRaises(a.AssetError):a.buy_for_company('g1','CAR-05','c1','userB')
    def test_9_sale_keeps_lease(self):
        asset=a.buy('g1','EST-04',101,'userA')
        order=a.offer('g1',asset,'character',101,'userA','rent',6000)
        contract=a.lease('g1',asset,103,'userC',1,order)
        sale=a.offer('g1',asset,'character',101,'userA','sale',700000)
        a.accept('g1',sale,102,'userB')
        with a.get_connection() as db:
            row=db.execute('SELECT landlord_id,status FROM rp_asset_leases WHERE id=?',(contract,)).fetchone()
        self.assertEqual(row['landlord_id'],'102')
        self.assertEqual(row['status'],'active')
    def test_10_insufficient_funds_rollback(self):
        with transaction() as db:
            db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(104,'g1','userD','無錢角色','active')")
        with self.assertRaises(Exception):a.buy('g1','AIR-03',104,'userD')
        self.assertFalse(any(x['template_code']=='AIR-03' for x in a.holdings('g1',104)))
    def balance(self,cid):
        with a.get_connection() as db:
            ac=get_or_create_account(db,'g1','character',str(cid),'wallet')
            return account_balance(db,'g1',ac)

if __name__=='__main__':unittest.main()
