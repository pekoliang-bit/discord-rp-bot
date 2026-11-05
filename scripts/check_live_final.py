"""直播收尾隔離回歸：python scripts/check_live_final.py

用原始資料庫、帳本與直播核心；官方商城 UI 使用最小測試替身（
CI 環境未安裝 discord.py）。正式上線仍須測試完整商城和 Discord。
"""
from __future__ import annotations
import tempfile
import types
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    from core import database
    from core import live_stream, live_commerce, live_shop_adapter, live_checkout
    from core.database import transaction, get_or_create_account, account_balance
    with tempfile.TemporaryDirectory() as folder:
        database.DATABASE_PATH = str(Path(folder) / 'live_test.sqlite3')
        database.init_db()
        database.register_guild('100', '測試')
        with transaction() as db:
            for cid, uid, name in [(1, '111', '主播'), (2, '222', '買家')]:
                db.execute("INSERT INTO characters(id,guild_id,owner_id,name,status) VALUES(?,?,?,?,'active')", (cid, '100', uid, name))
            db.execute('''CREATE TABLE shop_products(id INTEGER PRIMARY KEY,guild_id TEXT,name TEXT,price INTEGER,stock INTEGER,active INTEGER,scope TEXT,category TEXT,updated_at TEXT)''')
            db.execute("INSERT INTO shop_products VALUES(7,'100','舞台燈',100,5,1,'public','演藝','2026-11-01')")
            db.execute('''CREATE TABLE shop_inventory(guild_id TEXT,character_id INTEGER,product_id INTEGER,quantity INTEGER,PRIMARY KEY(guild_id,character_id,product_id))''')
            db.execute('''CREATE TABLE shop_events(guild_id TEXT,event_key TEXT,kind TEXT,character_id INTEGER,product_id INTEGER,quantity INTEGER,created_at TEXT,UNIQUE(guild_id,event_key))''')
            wallet = get_or_create_account(db,'100','character',2,'wallet')
            house = get_or_create_account(db,'100','system','test_funding','wallet')
            from core.economy import _payment
            _payment(db,'100','test-seed',house,wallet,1000,'隔離測試初始資金')
        # Only shop UI functions are stubbed; official ledger operations remain real.
        permissions = types.ModuleType('core.permissions')
        class Actor:
            def __init__(self,guild_id,user_id,is_admin=False):
                self.guild_id=str(guild_id); self.user_id=str(user_id); self.is_admin=is_admin
        permissions.Actor=Actor
        sys.modules['core.permissions']=permissions
        shop=types.ModuleType('modules.shop')
        def require_character(db,actor,cid):
            row=db.execute("SELECT * FROM characters WHERE guild_id=? AND owner_id=? AND id=? AND status='active'", (actor.guild_id,actor.user_id,cid)).fetchone()
            if row is None: raise PermissionError('角色持有人驗證失敗')
        shop.require_character=require_character
        shop.require_store_purchase_location=lambda db,gid,item: None
        shop.achievement_checkout_price=lambda db,gid,cid,item,qty:(item['price']*qty,None,0)
        def adjust_inventory(db,gid,cid,pid,qty):
            db.execute('''INSERT INTO shop_inventory(guild_id,character_id,product_id,quantity) VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity''',(gid,cid,pid,qty))
        shop.adjust_inventory=adjust_inventory
        shop.utc_now=lambda:'2026-11-01T00:00:00Z'
        shop.log_event=lambda db,gid,key,kind,cid,pid,qty:db.execute('INSERT INTO shop_events VALUES(?,?,?,?,?,?,?)',(gid,key,kind,cid,pid,qty,'2026-11-01'))
        modules=types.ModuleType('modules'); modules.__path__=[str(ROOT/'modules')]; modules.shop=shop
        sys.modules['modules']=modules; sys.modules['modules.shop']=shop

        sid=live_stream.start('100','111',1,'帶貨測試','sales','realtime')
        oid=live_commerce.propose_offer('100','111',sid,'7','舞台燈',100,3,1000)
        try:
            live_shop_adapter.verify_official_offer('100',oid)
            raise AssertionError('未授權驗證竟然通過')
        except PermissionError: pass
        live_shop_adapter.verify_official_offer('100',oid,authorized=True)
        first=live_checkout.checkout('100','222',2,oid,2,'checkout-1')
        assert first['paid_amount']==200 and first['commission_gross']==20 and first['platform_fee']==4 and first['commission_amount']==16
        same=live_checkout.checkout('100','222',2,oid,2,'checkout-1')
        assert same['id']==first['id']
        with database.get_connection() as db:
            buyer=get_or_create_account(db,'100','character',2,'wallet')
            host=get_or_create_account(db,'100','character',1,'wallet')
            assert account_balance(db,'100',buyer)==800
            assert account_balance(db,'100',host)==16
            assert db.execute('SELECT stock FROM shop_products WHERE id=7').fetchone()[0]==3
            assert db.execute('SELECT quantity FROM shop_inventory WHERE character_id=2 AND product_id=7').fetchone()[0]==2
        try:
            live_checkout.checkout('100','222',2,oid,2,'checkout-2')
            raise AssertionError('超出帶貨數量上限未阻擋')
        except ValueError: pass
        summary=live_checkout.order_totals('100',sid)
        assert summary['orders']==1 and summary['units']==2 and summary['sales']==200 and summary['commissions']==16
        print('PASS: 官方驗證權限、購買與分潤、原子帳本、庫存、重複交易、限額、業績')


if __name__=='__main__':
    run()
