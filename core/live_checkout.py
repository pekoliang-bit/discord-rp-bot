"""直播帶貨真實結算：官方商城付款／庫存／背包／佣金原子交易。

絕不另外鑄造金幣；所有帳本、商品交易與訂單於同一 SQLite
BEGIN IMMEDIATE 完成。再次使用同一個 interaction id 不重複扣款。
此 V1 僅支援官方商城，跨公司和玩家店舖合約需要未來的專用適配器。
"""
from __future__ import annotations
import time
from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment
from core import live_commerce as commerce
from core.live_shop_adapter import _official_product

ORDERS_DDL = """CREATE TABLE IF NOT EXISTS rp_live_sale_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    offer_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    buyer_character_id INTEGER NOT NULL,
    host_character_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
    paid_amount INTEGER NOT NULL CHECK(paid_amount >= 0),
    commission_amount INTEGER NOT NULL CHECK(commission_amount >= 0),
    commission_gross INTEGER NOT NULL DEFAULT 0,
    platform_fee INTEGER NOT NULL DEFAULT 0,
    request_key TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(guild_id, request_key)
)"""


def _init(db):
    commerce._init(db)
    db.execute(ORDERS_DDL)
    cols={r['name'] for r in db.execute('PRAGMA table_info(rp_live_sale_orders)').fetchall()}
    if 'commission_gross' not in cols:
        db.execute('ALTER TABLE rp_live_sale_orders ADD COLUMN commission_gross INTEGER NOT NULL DEFAULT 0')
    if 'platform_fee' not in cols:
        db.execute('ALTER TABLE rp_live_sale_orders ADD COLUMN platform_fee INTEGER NOT NULL DEFAULT 0')


def checkout(guild_id, user_id, buyer_character_id, offer_id, quantity, request_key, *, now=None):
    """玩家點選購買後呼叫；只有官方商城已驗證商品可購買。"""
    from modules import shop  # 延遲 import：既有商城依賴 discord.py
    from core.permissions import Actor

    gid, uid, buyer_id = str(guild_id), str(user_id), int(buyer_character_id)
    if type(quantity) is not int or not 1 <= quantity <= 99:
        raise ValueError('購買數量必須介於 1～99')
    key = f'live-sale:{request_key}'
    if len(key) > 180 or not str(request_key).strip():
        raise ValueError('交易識別碼無效')
    with transaction() as db:
        _init(db)
        existing = db.execute('SELECT * FROM rp_live_sale_orders WHERE guild_id=? AND request_key=?', (gid, key)).fetchone()
        if existing:
            if existing['buyer_character_id'] != buyer_id or existing['offer_id'] != int(offer_id) or existing['quantity'] != quantity:
                raise ValueError('交易識別碼與原本請求不符')
            return dict(existing)

        actor = Actor(guild_id=gid, user_id=uid, is_admin=False)
        shop.require_character(db, actor, buyer_id)
        offer = db.execute('''SELECT o.*,s.status AS live_status FROM rp_live_sale_offers o
            JOIN rp_live_sessions s ON s.guild_id=o.guild_id AND s.id=o.session_id
            WHERE o.guild_id=? AND o.id=?''', (gid, int(offer_id))).fetchone()
        if not offer or offer['status'] != 'verified' or offer['live_status'] != 'live':
            raise ValueError('直播已結束或商品尚未驗證')
        product = _official_product(db, gid, offer['product_ref'])
        if product['scope'] != 'public':
            raise ValueError('黑卡限定商品不可由此入口結算')
        if product['name'] != offer['title'] or product['price'] != offer['unit_price']:
            raise ValueError('商品名稱或價格已修改，請官方重新確認')
        if buyer_id == int(offer['host_character_id']):
            raise ValueError('不可向自己帶貨的商品下單')
        prior_qty = db.execute('SELECT COALESCE(SUM(quantity),0) FROM rp_live_sale_orders WHERE guild_id=? AND offer_id=?',
                               (gid, int(offer_id))).fetchone()[0]
        if prior_qty + quantity > int(offer['stock_cap']):
            raise ValueError('直播可售數量不足')
        if product['stock'] is not None and int(product['stock']) < quantity:
            raise ValueError('官方商城庫存不足')

        shop.require_store_purchase_location(db, gid, product)
        cost, _, _ = shop.achievement_checkout_price(db, gid, buyer_id, product, quantity)
        cost = int(cost)
        if cost <= 0:
            raise ValueError('零元商品不適用直播佣金結算')
        gross_commission = (cost * int(offer['commission_bp'])) // 10000
        platform_fee = (gross_commission * 20) // 100
        commission = gross_commission - platform_fee
        payer = get_or_create_account(db, gid, 'character', buyer_id, 'wallet')
        merchant = get_or_create_account(db, gid, 'system', 'official_shop', 'treasury')
        host = get_or_create_account(db, gid, 'character', int(offer['host_character_id']), 'wallet')
        _payment(db, gid, f'shop:buy:{key}', payer, merchant, cost, f'直播官方商城購買 #{offer_id} ×{quantity}')
        if commission:
            _payment(db, gid, f'shop:commission:{key}', merchant, host, commission, f'直播佣金（平台抽成後） #{offer_id}')
        if platform_fee:
            platform = get_or_create_account(db, gid, 'system', 'live_platform', 'wallet')
            _payment(db, gid, f'shop:platform:{key}', merchant, platform, platform_fee, f'直播帶貨平台抽成 #{offer_id}')
        shop.adjust_inventory(db, gid, buyer_id, int(product['id']), quantity)
        if product['stock'] is not None:
            db.execute('UPDATE shop_products SET stock=stock-?,updated_at=? WHERE guild_id=? AND id=?',
                       (quantity, shop.utc_now(), gid, int(product['id'])))
        shop.log_event(db, gid, f'buy:{key}', 'purchase', buyer_id, int(product['id']), quantity)
        order_id = db.execute('''INSERT INTO rp_live_sale_orders
          (guild_id,offer_id,session_id,product_id,buyer_character_id,host_character_id,
           quantity,paid_amount,commission_amount,commission_gross,platform_fee,request_key,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (gid,int(offer_id),int(offer['session_id']),int(product['id']),buyer_id,
           int(offer['host_character_id']),quantity,cost,commission,gross_commission,platform_fee,key,int(time.time() if now is None else now))).lastrowid
        return dict(db.execute('SELECT * FROM rp_live_sale_orders WHERE id=?', (order_id,)).fetchone())


def order_totals(guild_id, session_id):
    with get_connection() as db:
        _init(db)
        row = db.execute('''SELECT COUNT(*) AS orders, COALESCE(SUM(quantity),0) AS units,
            COALESCE(SUM(paid_amount),0) AS sales, COALESCE(SUM(commission_amount),0) AS commissions, COALESCE(SUM(platform_fee),0) AS platform_fees
            FROM rp_live_sale_orders WHERE guild_id=? AND session_id=?''', (str(guild_id), int(session_id))).fetchone()
        return dict(row)
