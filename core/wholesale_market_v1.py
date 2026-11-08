"""/商城 批發市場：官方＋玩家品牌實物來源、買斷與寄售庫存。

嚴格使用既有 shop_products 中的已核准商品作為 SKU。
品牌自有新商品在正式商品入庫以前不得偽造 SKU 或向買家收費。
"""
from __future__ import annotations
from core.database import get_connection,transaction,get_or_create_account,account_balance
from core.economy import _payment

DDL=(
    '''CREATE TABLE IF NOT EXISTS rp_wholesale_offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        supplier_kind TEXT NOT NULL CHECK(supplier_kind IN ('official','brand')),
        supplier_business_id TEXT, supplier_character_id INTEGER,
        product_id INTEGER NOT NULL, retail_price INTEGER NOT NULL,
        wholesale_price INTEGER NOT NULL, stock INTEGER NOT NULL,
        mode TEXT NOT NULL CHECK(mode IN ('buyout','consignment')),
        host_commission_bp INTEGER NOT NULL DEFAULT 2000,
        status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(stock>=0 AND wholesale_price>0 AND retail_price>0),
        CHECK(host_commission_bp BETWEEN 500 AND 5000))''',
    '''CREATE TABLE IF NOT EXISTS rp_wholesale_warehouse (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        owner_character_id INTEGER NOT NULL, offer_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL, mode TEXT NOT NULL,
        stock INTEGER NOT NULL CHECK(stock>=0), original_qty INTEGER NOT NULL,
        unit_cost INTEGER NOT NULL, retail_price INTEGER NOT NULL,
        supplier_kind TEXT NOT NULL, supplier_business_id TEXT,
        commission_bp INTEGER NOT NULL, acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
    '''CREATE TABLE IF NOT EXISTS rp_wholesale_acquisitions (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        offer_id INTEGER NOT NULL, owner_character_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL, amount_paid INTEGER NOT NULL,
        warehouse_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,request_key))''',
)

def initialize(db):
    for ddl in DDL: db.execute(ddl)

def _product(db,gid,product_id):
    row=db.execute('SELECT * FROM shop_products WHERE guild_id=? AND id=? AND active=1 AND scope=\'public\'',(str(gid),int(product_id))).fetchone()
    if not row or int(row['price']) <= 0: raise ValueError('商品必須是已上架的公開商城商品，且價格大於0')
    return row

def _active_character(db,gid,user_id,cid):
    row=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",(str(gid),int(cid),str(user_id))).fetchone()
    if not row: raise PermissionError('必須使用自己的已啟用角色')

def _reserve_stock(db,gid,cid,product_id,qty):
    row=db.execute('SELECT quantity FROM shop_inventory WHERE guild_id=? AND character_id=? AND product_id=?',(str(gid),int(cid),int(product_id))).fetchone()
    if not row or int(row['quantity'])<qty: raise ValueError('供應角色背包沒有足夠的商品實物')
    db.execute('UPDATE shop_inventory SET quantity=quantity-? WHERE guild_id=? AND character_id=? AND product_id=?',(qty,str(gid),int(cid),int(product_id)))

def publish(guild_id,user_id,supplier_character_id,product_id,wholesale_price,quantity,mode,
            *,business_id=None,commission_bp=2000,official=False):
    """官方報價由群主核驗；品牌必須是有實物庫存的正式品牌擁有者。"""
    if mode not in ('buyout','consignment'): raise ValueError('供貨模式錯誤')
    if type(quantity) is not int or not 1<=quantity<=100000: raise ValueError('庫存須為1～100000')
    if type(wholesale_price) is not int or wholesale_price<1: raise ValueError('批發價須為正整數')
    if not 500<=int(commission_bp)<=5000: raise ValueError('寄售佣金須介於5%～50%')
    with transaction() as db:
        initialize(db)
        p=_product(db,guild_id,product_id)
        # 40%-90% relative pricing; a proposal outside range needs manual approval, not accepted directly.
        if not .4*int(p['price'])<=wholesale_price<=.9*int(p['price']):
            raise ValueError('批發價須在核准售價40%～90%內；例外報價請官方審核')
        if official: raise PermissionError('官方貨源請由群主使用正式驗證入口發布')
        _active_character(db,guild_id,user_id,supplier_character_id)
        brand=db.execute("SELECT business_id FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='brand' AND status='active' AND owner_id=?",(str(guild_id),str(business_id),str(user_id))).fetchone()
        if not brand: raise PermissionError('只有已核准的品牌持有人能掛售品牌商品')
        _reserve_stock(db,guild_id,supplier_character_id,product_id,quantity)
        return db.execute('''INSERT INTO rp_wholesale_offers (guild_id,supplier_kind,supplier_business_id,supplier_character_id,
            product_id,retail_price,wholesale_price,stock,mode,host_commission_bp)
            VALUES (?,'brand',?,?,?,?,?,?,?,?)''',
            (str(guild_id),str(business_id),int(supplier_character_id),int(product_id),int(p['price']),wholesale_price,quantity,mode,int(commission_bp))).lastrowid

def publish_official_verified(guild_id,product_id,wholesale_price,quantity,*,verified_owner=False):
    if not verified_owner: raise PermissionError('必須確認 Discord 伺服器群主身分')
    if type(quantity) is not int or not 1<=quantity<=100000: raise ValueError('供貨數量錯誤')
    with transaction() as db:
        initialize(db)
        p=_product(db,guild_id,product_id)
        if not .4*int(p['price'])<=int(wholesale_price)<=.9*int(p['price']): raise ValueError('批發價須介於零售價40%～90%')
        if p['stock'] is not None:
            cur=db.execute('UPDATE shop_products SET stock=stock-? WHERE guild_id=? AND id=? AND stock>=?',(quantity,str(guild_id),int(product_id),quantity))
            if cur.rowcount!=1: raise ValueError('官方商城庫存不足')
        return db.execute("""INSERT INTO rp_wholesale_offers(guild_id,supplier_kind,product_id,retail_price,
            wholesale_price,stock,mode) VALUES(?,'official',?,?,?,?, 'buyout')""",
            (str(guild_id),int(product_id),int(p['price']),int(wholesale_price),quantity)).lastrowid

def list_offers(guild_id):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT o.*,p.name AS product_name FROM rp_wholesale_offers o
            JOIN shop_products p ON p.guild_id=o.guild_id AND p.id=o.product_id
            WHERE o.guild_id=? AND o.status='active' AND o.stock>0 ORDER BY o.id DESC LIMIT 50''',(str(guild_id),)).fetchall()]

def acquire(guild_id,user_id,host_character_id,offer_id,quantity,request_key):
    if type(quantity) is not int or not 1<=quantity<=10000: raise ValueError('數量須介於1～10000')
    key=f'wholesale:{request_key}'
    with transaction() as db:
        initialize(db)
        prior=db.execute('SELECT * FROM rp_wholesale_acquisitions WHERE guild_id=? AND request_key=?',(str(guild_id),key)).fetchone()
        if prior:
            if int(prior['owner_character_id'])!=int(host_character_id) or int(prior['offer_id'])!=int(offer_id) or int(prior['quantity'])!=quantity: raise ValueError('重複交易識別碼與原訂單不符')
            return dict(prior)
        _active_character(db,guild_id,user_id,host_character_id)
        o=db.execute("SELECT * FROM rp_wholesale_offers WHERE guild_id=? AND id=? AND status='active'",(str(guild_id),int(offer_id))).fetchone()
        if not o or int(o['stock'])<quantity: raise ValueError('供貨商品不存在或庫存不足')
        if o['supplier_character_id']==int(host_character_id): raise ValueError('不能向自己進貨')
        total=quantity*int(o['wholesale_price']) if o['mode']=='buyout' else 0
        if total:
            payer=get_or_create_account(db,str(guild_id),'character',int(host_character_id),'wallet')
            receiver=get_or_create_account(db,str(guild_id),'system','official_shop','treasury') if o['supplier_kind']=='official' else get_or_create_account(db,str(guild_id),'company',str(o['supplier_business_id']),'treasury')
            if account_balance(db,str(guild_id),payer)<total: raise ValueError('金幣不足')
            _payment(db,str(guild_id),f'wholesale:buy:{key}',payer,receiver,total,'批發市場買斷進貨')
        db.execute('UPDATE rp_wholesale_offers SET stock=stock-? WHERE guild_id=? AND id=?',(quantity,str(guild_id),int(offer_id)))
        wid=db.execute('''INSERT INTO rp_wholesale_warehouse(guild_id,owner_character_id,offer_id,product_id,mode,stock,original_qty,
            unit_cost,retail_price,supplier_kind,supplier_business_id,commission_bp)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (str(guild_id),int(host_character_id),int(offer_id),int(o['product_id']),str(o['mode']),quantity,quantity,int(o['wholesale_price']),int(o['retail_price']),str(o['supplier_kind']),o['supplier_business_id'],int(o['host_commission_bp']))).lastrowid
        db.execute('INSERT INTO rp_wholesale_acquisitions(guild_id,request_key,offer_id,owner_character_id,quantity,amount_paid,warehouse_id) VALUES(?,?,?,?,?,?,?)',(str(guild_id),key,int(offer_id),int(host_character_id),quantity,total,wid))
        return {'warehouse_id':wid,'quantity':quantity,'amount_paid':total,'mode':str(o['mode'])}

def warehouse(guild_id,character_id):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT w.*,p.name AS product_name FROM rp_wholesale_warehouse w
            JOIN shop_products p ON p.guild_id=w.guild_id AND p.id=w.product_id
            WHERE w.guild_id=? AND w.owner_character_id=? AND w.stock>0 ORDER BY w.id DESC''',(str(guild_id),int(character_id))).fetchall()]

def ensure_sales_ready(guild_id,host_character_id):
    rows=warehouse(guild_id,host_character_id)
    if len({int(r['product_id']) for r in rows})<5:
        raise ValueError('直播帶貨需先到 /商城 → 批發市場進貨至少5種不同商品')
    return rows

def prepare_live_session_in_transaction(db,gid,session_id,host_character_id):
    """開播同一交易內自動上架倉庫所有可售批次；至少五種不同 SKU。"""
    from core import live_commerce
    initialize(db);live_commerce._init(db)
    lots=db.execute('''SELECT * FROM rp_wholesale_warehouse WHERE guild_id=? AND owner_character_id=? AND stock>0
        ORDER BY id''',(str(gid),int(host_character_id))).fetchall()
    if len({int(x['product_id']) for x in lots})<5:
        raise ValueError('帶貨直播需先到 /商城 → 批發市場進貨至少5種不同商品')
    for lot in lots:
        p=_product(db,gid,lot['product_id'])
        # Distinct lots can share SKU; indexed by warehouse ID rather than product ID.
        db.execute('''INSERT INTO rp_live_sale_offers(guild_id,session_id,host_character_id,product_ref,title,
            unit_price,stock_cap,commission_bp,status,created_at)
            VALUES(?,?,?,?,?,?,?,?, 'verified',strftime('%s','now'))''',
            (str(gid),int(session_id),int(host_character_id),f"warehouse:{lot['id']}",str(p['name']),int(lot['retail_price']),int(lot['stock']),int(lot['commission_bp'])))

def checkout_warehouse_in_transaction(db,gid,uid,buyer_id,offer,product,quantity,key,ts):
    """已驗證買家與進行中直播；商品進貨/付款/交貨/熱度同一交易。"""
    from modules import shop
    from core import live_rankings_v11
    wid=int(str(offer['product_ref']).split(':',1)[1])
    lot=db.execute('SELECT * FROM rp_wholesale_warehouse WHERE guild_id=? AND id=?',(str(gid),wid)).fetchone()
    if not lot or int(lot['owner_character_id'])!=int(offer['host_character_id']): raise ValueError('商品庫存歸屬不符')
    if int(lot['product_id'])!=int(product['id']) or int(lot['retail_price'])!=int(offer['unit_price']): raise ValueError('商品定價或識別碼不符')
    if int(lot['stock'])<quantity: raise ValueError('帶貨倉庫庫存不足')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_wholesale_sale_refunds (
        guild_id TEXT NOT NULL, order_id INTEGER NOT NULL, buyer_character_id INTEGER NOT NULL,
        refunded_amount INTEGER NOT NULL, refunded_at INTEGER NOT NULL,
        PRIMARY KEY(guild_id,order_id))''')
    sold=db.execute('''SELECT COALESCE(SUM(s.quantity),0) FROM rp_live_sale_orders s
        WHERE s.guild_id=? AND s.offer_id=? AND NOT EXISTS
        (SELECT 1 FROM rp_wholesale_sale_refunds r WHERE r.guild_id=s.guild_id AND r.order_id=s.id)''',
        (str(gid),int(offer['id']))).fetchone()[0]
    if int(sold)+quantity>int(offer['stock_cap']): raise ValueError('直播上架限額不足')
    if int(buyer_id)==int(offer['host_character_id']): raise ValueError('不能購買自己的直播商品')
    # Prevent self-dealing between the buyer and the supplier (brand-owned goods).
    if lot['supplier_kind']=='brand':
        supplier=db.execute('SELECT owner_id FROM rp_business_entities WHERE guild_id=? AND business_id=?',(str(gid),str(lot['supplier_business_id']))).fetchone()
        if supplier and str(supplier['owner_id'])==str(uid): raise ValueError('不能從自己擁有的品牌自買自賣')
    gross=int(lot['retail_price'])*quantity
    fee=gross//5
    net=gross-fee
    if lot['mode']=='consignment':
        host_amount=(net*int(lot['commission_bp']))//10000
        supplier_amount=net-host_amount
    else:
        host_amount=net
        supplier_amount=0
    payer=get_or_create_account(db,str(gid),'character',int(buyer_id),'wallet')
    if account_balance(db,str(gid),payer)<gross: raise ValueError('角色錢包金幣不足')
    platform=get_or_create_account(db,str(gid),'system','live_platform','wallet')
    host=get_or_create_account(db,str(gid),'character',int(offer['host_character_id']),'wallet')
    if fee: _payment(db,str(gid),f'whsale:platform:{key}',payer,platform,fee,'直播成交額20%平台費')
    if host_amount: _payment(db,str(gid),f'whsale:host:{key}',payer,host,host_amount,'直播商品成交主播分潤')
    if supplier_amount:
        receiver=get_or_create_account(db,str(gid),'company',str(lot['supplier_business_id']),'treasury') if lot['supplier_kind']=='brand' else get_or_create_account(db,str(gid),'system','official_shop','treasury')
        _payment(db,str(gid),f'whsale:supplier:{key}',payer,receiver,supplier_amount,'直播寄售品牌分潤')
    shop.adjust_inventory(db,str(gid),int(buyer_id),int(product['id']),quantity)
    db.execute('UPDATE rp_wholesale_warehouse SET stock=stock-? WHERE guild_id=? AND id=?',(quantity,str(gid),wid))
    order_id=db.execute('''INSERT INTO rp_live_sale_orders(guild_id,offer_id,session_id,product_id,buyer_character_id,
        host_character_id,quantity,paid_amount,commission_amount,commission_gross,platform_fee,request_key,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (str(gid),int(offer['id']),int(offer['session_id']),int(product['id']),int(buyer_id),int(offer['host_character_id']),quantity,gross,host_amount,host_amount,fee,key,int(ts))).lastrowid
    live_rankings_v11.initialize(db)
    live_rankings_v11._record(db,gid,f'sale:{order_id}',int(offer['session_id']),gross//100,'sale',ts)
    return dict(db.execute('SELECT * FROM rp_live_sale_orders WHERE guild_id=? AND id=?',(str(gid),order_id)).fetchone())

REFUND_DDL='''CREATE TABLE IF NOT EXISTS rp_wholesale_sale_refunds (
    guild_id TEXT NOT NULL, order_id INTEGER NOT NULL, buyer_character_id INTEGER NOT NULL,
    refunded_amount INTEGER NOT NULL, refunded_at INTEGER NOT NULL,
    PRIMARY KEY(guild_id,order_id))'''

def refund_sale(guild_id,user_id,buyer_character_id,order_id,*,now=None):
    """僅自動退未使用的普通實物；原子撤回貨物、收入及排行成交熱度。

    消耗/已啟動效果、逾期或分潤帳戶餘額不足的交易交由正式售後審核。
    """
    import time
    from core import live_rankings_v11
    from modules import shop
    gid=str(guild_id);ts=int(time.time() if now is None else now)
    with transaction() as db:
        initialize(db);live_rankings_v11.initialize(db)
        from core import live_checkout
        live_checkout._init(db)
        db.execute(REFUND_DDL)
        _active_character(db,gid,user_id,buyer_character_id)
        o=db.execute('''SELECT s.*,f.product_ref FROM rp_live_sale_orders s JOIN rp_live_sale_offers f
            ON f.guild_id=s.guild_id AND f.id=s.offer_id
            WHERE s.guild_id=? AND s.id=? AND s.buyer_character_id=?''',(gid,int(order_id),int(buyer_character_id))).fetchone()
        if not o or not str(o['product_ref']).startswith('warehouse:'):
            raise ValueError('只支援批發市場成交的未使用普通商品自助退款')
        if ts-int(o['created_at'])>86400:raise ValueError('已超過24小時自助退款期間，請聯絡官方處理')
        if db.execute('SELECT 1 FROM rp_wholesale_sale_refunds WHERE guild_id=? AND order_id=?',(gid,int(order_id))).fetchone():
            raise ValueError('這筆訂單已退款')
        p=_product(db,gid,o['product_id'])
        if str(p['effect_kind'])!='none':
            raise ValueError('有消耗或屬性效果的商品必須人工審核退款')
        held=db.execute('SELECT quantity FROM shop_inventory WHERE guild_id=? AND character_id=? AND product_id=?',
            (gid,int(buyer_character_id),int(o['product_id']))).fetchone()
        if not held or int(held['quantity'])<int(o['quantity']):raise ValueError('商品已使用、轉讓或庫存不足，無法自助退貨')
        lot_id=int(str(o['product_ref']).split(':',1)[1])
        lot=db.execute('SELECT * FROM rp_wholesale_warehouse WHERE guild_id=? AND id=?',(gid,lot_id)).fetchone()
        if not lot:raise ValueError('原進貨批次不存在，需人工審核')
        gross=int(o['paid_amount']);fee=int(o['platform_fee']);host_pay=int(o['commission_amount']);supplier_pay=gross-fee-host_pay
        payer=get_or_create_account(db,gid,'character',int(buyer_character_id),'wallet')
        host=get_or_create_account(db,gid,'character',int(o['host_character_id']),'wallet')
        platform=get_or_create_account(db,gid,'system','live_platform','wallet')
        supplier=(get_or_create_account(db,gid,'company',str(lot['supplier_business_id']),'treasury') if lot['supplier_kind']=='brand'
                  else get_or_create_account(db,gid,'system','official_shop','treasury'))
        for account,amount in ((host,host_pay),(platform,fee),(supplier,supplier_pay)):
            if amount and account_balance(db,gid,account)<amount and account not in (platform,supplier):
                raise ValueError('收益方餘額不足以自助退款，請由官方處理')
        if host_pay:_payment(db,gid,f'whsale:refund:host:{order_id}',host,payer,host_pay,'帶貨商品退款收回主播收益')
        if fee:_payment(db,gid,f'whsale:refund:platform:{order_id}',platform,payer,fee,'退還平台服務費')
        if supplier_pay:_payment(db,gid,f'whsale:refund:supplier:{order_id}',supplier,payer,supplier_pay,'帶貨商品寄售退款')
        shop.adjust_inventory(db,gid,int(buyer_character_id),int(o['product_id']),-int(o['quantity']))
        db.execute('UPDATE rp_wholesale_warehouse SET stock=stock+? WHERE guild_id=? AND id=?',
                   (int(o['quantity']),gid,lot_id))
        db.execute('INSERT INTO rp_wholesale_sale_refunds VALUES (?,?,?, ?,?)',
                   (gid,int(order_id),int(buyer_character_id),gross,ts))
        live_rankings_v11.reverse_heat_in_transaction(db,gid,f'sale:{order_id}',f'refund:{order_id}',ts)
        return {'order_id':int(order_id),'refund_amount':gross,'returned_stock':int(o['quantity'])}
