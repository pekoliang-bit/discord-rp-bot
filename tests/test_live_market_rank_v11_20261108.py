"""直播批發、庫存、平台抽成、進房熱度和評論分類的隔離 SQLite 測試。"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from core import config,database,live_stream,live_commerce
from core import wholesale_market_v1 as market,live_rankings_v11 as ranks
from core.database import get_or_create_account,account_balance
from core.economy import _payment
from core.network_post_categories import classify_forum_tags,SUGGESTED_TAGS

class LiveMarketV11Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj,key in ((config,'DATABASE_PATH'),(database,'DATABASE_PATH')):
            p=patch.object(obj,key,os.path.join(self.tmp.name,'test.db'))
            p.start();self.addCleanup(p.stop)
        database.init_db()
        with database.transaction() as db:
            db.execute("INSERT INTO guilds(guild_id,name) VALUES('123','test')")
            for uid in (101,102,103):
                db.execute("INSERT INTO characters(guild_id,owner_id,name,status) VALUES('123',?,?, 'active')",(str(uid),f'user{uid}'))
            db.execute('''CREATE TABLE shop_products(id INTEGER PRIMARY KEY,guild_id TEXT,name TEXT,
                price INTEGER,stock INTEGER,scope TEXT,active INTEGER,effect_kind TEXT DEFAULT 'none')''')
            db.execute('''CREATE TABLE shop_inventory(guild_id TEXT,character_id INTEGER,product_id INTEGER,quantity INTEGER,
                PRIMARY KEY(guild_id,character_id,product_id))''')
            db.execute('''CREATE TABLE rp_business_entities(guild_id TEXT,business_id TEXT,kind TEXT,status TEXT,owner_id TEXT,brand_tier TEXT)''')
            for i in range(1,8):
                db.execute('INSERT INTO shop_products(id,guild_id,name,price,stock,scope,active) VALUES(?,?,?,?,?,?,?)',(i,'123',f'商品{i}',1000,100,'public',1))
            system=get_or_create_account(db,'123','system','testing','wallet')
            for cid in (1,2,3):
                user=get_or_create_account(db,'123','character',cid,'wallet')
                _payment(db,'123',f'seed:{cid}',system,user,300000,'測試存款')
        self.gid='123'

    def _stock_five(self):
        for pid in range(1,6):
            offer=market.publish_official_verified(self.gid,pid,700,20,verified_owner=True)
            market.acquire(self.gid,101,1,offer,5,f'buy:{pid}')

    def test_five_skus_required_for_sales(self):
        with self.assertRaisesRegex(ValueError,'5種'):
            live_stream.start(123,101,1,'sales','sales','realtime',now=180000)
        self._stock_five()
        sid=live_stream.start(123,101,1,'sales','sales','realtime',now=180000)
        with database.get_connection() as db:
            offers=db.execute("SELECT * FROM rp_live_sale_offers WHERE guild_id=? AND session_id=?",('123',sid)).fetchall()
            self.assertEqual(len(offers),5)
            self.assertTrue(all(x['status']=='verified' for x in offers))
            self.assertTrue(all(x['product_ref'].startswith('warehouse:') for x in offers))

    def test_atomic_acquire_and_idempotency(self):
        offer=market.publish_official_verified(self.gid,1,700,30,verified_owner=True)
        before=self._balance('character',1)
        a=market.acquire(self.gid,101,1,offer,5,'same')
        b=market.acquire(self.gid,101,1,offer,5,'same')
        self.assertEqual(a['warehouse_id'],b['warehouse_id'])
        self.assertEqual(self._balance('character',1),before-3500)
        with self.assertRaises(ValueError):market.acquire(self.gid,101,1,offer,6,'same')

    def _balance(self,typ,id):
        with database.get_connection() as db:
            return account_balance(db,'123',get_or_create_account(db,'123',typ,id,'wallet'))

    def test_audience_unique_hour_and_levels(self):
        sid=live_stream.start(123,101,1,'personal','personal','realtime',now=720000)
        self.assertEqual(ranks.view(123,1,sid,2,now=720010),60)
        self.assertEqual(ranks.view(123,1,sid,2,now=720011),0)
        self.assertEqual(ranks.audience_bonus(20),3000)
        self.assertEqual(ranks.audience_bonus(16),700)
        self.assertEqual(ranks.room(123,1)['total_heat'],60)

    def test_official_promotion_real_debit(self):
        sid=live_stream.start(123,101,1,'personal','personal','realtime',now=720000)
        before=self._balance('character',1)
        first=ranks.promote(123,101,1,sid,1000,'p1',now=720011)
        again=ranks.promote(123,101,1,sid,1000,'p1',now=720011)
        self.assertEqual(first['heat'],100)
        self.assertTrue(again['already_done'])
        self.assertEqual(self._balance('character',1),before-1000)
        with self.assertRaises(PermissionError):ranks.promote(123,102,1,sid,1000,'bad',now=720012)

    def test_brand_offer_needs_real_stock(self):
        with database.transaction() as db:
            db.execute("INSERT INTO rp_business_entities VALUES('123','BR001','brand','active','102','輕奢')")
            db.execute("INSERT INTO shop_inventory VALUES('123',2,2,7)")
        offer=market.publish('123',102,2,2,700,5,'consignment',business_id='BR001')
        self.assertIsInstance(offer,int)
        with database.get_connection() as db:
            qty=db.execute("SELECT quantity FROM shop_inventory WHERE guild_id='123' AND character_id=2 AND product_id=2").fetchone()[0]
            self.assertEqual(qty,2)
        lot=market.acquire('123',101,1,offer,3,'consign1')
        self.assertEqual(lot['amount_paid'],0)
        self.assertEqual(lot['mode'],'consignment')

    def test_warehouse_checkout_and_refund_are_atomic(self):
        from unittest.mock import patch
        import types
        # Only replace the discord-dependent shop UI with its real SQL inventory contract.
        # The production money/ledger and live checkout code is exercised unchanged.
        shop=types.ModuleType('modules.shop')
        def require_character(db,actor,cid):
            row=db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'",(str(actor.guild_id),int(cid))).fetchone()
            if not row or str(row['owner_id'])!=str(actor.user_id):raise PermissionError('not owner')
        def adjust(db,gid,cid,pid,delta):
            row=db.execute('SELECT quantity FROM shop_inventory WHERE guild_id=? AND character_id=? AND product_id=?',(str(gid),int(cid),int(pid))).fetchone()
            qty=(int(row[0]) if row else 0)+int(delta)
            if qty<0: raise ValueError('insufficient')
            db.execute('INSERT INTO shop_inventory(guild_id,character_id,product_id,quantity) VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id,product_id) DO UPDATE SET quantity=excluded.quantity',
                (str(gid),int(cid),int(pid),qty))
        shop.require_character=require_character
        shop.adjust_inventory=adjust
        perms=types.ModuleType('core.permissions')
        class Actor:
            def __init__(self,guild_id,user_id,is_admin=False):
                self.guild_id=guild_id;self.user_id=user_id;self.is_admin=is_admin
        perms.Actor=Actor
        self._stock_five()
        sid=live_stream.start(123,101,1,'sales','sales','realtime',now=180000)
        from core import live_checkout
        with database.get_connection() as db:
            offer=db.execute('SELECT id FROM rp_live_sale_offers WHERE guild_id=? AND session_id=? LIMIT 1',('123',sid)).fetchone()[0]
        before=self._balance('character',2)
        with patch.dict(sys.modules,{'modules.shop':shop,'core.permissions':perms}):
            order=live_checkout.checkout(123,102,2,offer,1,'purchase-01',now=180010)
            repeat=live_checkout.checkout(123,102,2,offer,1,'purchase-01',now=180010)
            self.assertEqual(order['id'],repeat['id'])
            self.assertEqual(order['platform_fee'],200)
            self.assertEqual(order['commission_amount'],800)
            self.assertEqual(self._balance('character',2),before-1000)
            self.assertEqual(ranks.room(123,1)['total_heat'],10)
            ref=market.refund_sale(123,102,2,order['id'],now=180020)
            self.assertEqual(ref['refund_amount'],1000)
            self.assertEqual(self._balance('character',2),before)
            self.assertEqual(ranks.room(123,1)['total_heat'],0)
            with self.assertRaises(ValueError):market.refund_sale(123,102,2,order['id'],now=180021)

    def test_brand_entry_verified_once_per_hour(self):
        with database.transaction() as db:
            db.execute("INSERT INTO rp_business_entities VALUES('123','BR001','brand','active','102','輕奢')")
        sid=live_stream.start(123,101,1,'normal','personal','realtime',now=720000)
        self.assertEqual(ranks.brand_visit(123,102,'BR001',sid,now=720005),1500)
        self.assertEqual(ranks.brand_visit(123,102,'BR001',sid,now=720006),0)
        with self.assertRaises(PermissionError):ranks.brand_visit(123,103,'BR001',sid,now=720007)

    def test_120_hour_history_snapshot_survives_multiple_cycles(self):
        sid=live_stream.start(123,101,1,'one','personal','realtime',now=720000)
        ranks.view(123,1,sid,2,now=720001)
        result=ranks.period_rows(123,now=720000+3*ranks.PERIOD)
        self.assertEqual(result['rows'],[])
        with database.get_connection() as db:
            snaps=db.execute('SELECT position,heat FROM rp_live_heat_period_snapshots WHERE guild_id=?',('123',)).fetchall()
            self.assertEqual(len(snaps),1)
            self.assertEqual(snaps[0]['heat'],60)

    def test_sales_fifth_sku_and_frequency_risk_coexist(self):
        self._stock_five()
        for i in range(6):
            ts=1000000+i*100
            sid=live_stream.start(123,101,1,'sale','sales','realtime',now=ts)
            live_stream.stop(123,101,sid,now=ts+1)
        self.assertEqual(len(live_stream.frequency_status(123,1,now=1000600)['risk_events']),1)

    def test_tag_catalog_matches_reviewed_library(self):
        self.assertEqual(len(SUGGESTED_TAGS),12)
        self.assertEqual(classify_forum_tags(['日常生活','商業活動']), (1,4))
        self.assertEqual(classify_forum_tags(['自行創造的標籤']),())

if __name__=='__main__':unittest.main()
