"""直播帶貨：既有官方商城商品驗證器。此檔不註冊 Discord 指令。

V1 僅支援正式 shop_products（官方商城）商品；玩家商店及公司品牌
有獨立訂單／分潤流程，未接入前不可偽裝成官方商城商品。
"""
from __future__ import annotations

from core.database import transaction, get_connection
from core import live_commerce as commerce


def _official_product(db, guild_id: str, product_ref: str):
    if not str(product_ref).isdigit() or int(product_ref) <= 0:
        raise ValueError('目前僅支援官方商城商品數字 ID；玩家商店暫未接入')
    row = db.execute('SELECT * FROM shop_products WHERE guild_id=? AND id=?',
                     (guild_id, int(product_ref))).fetchone()
    if row is None or not row['active']:
        raise ValueError('官方商城商品不存在或已下架')
    return row


def verify_official_offer(guild_id, offer_id, *, authorized: bool = False):
    """僅允許已驗證 Discord 群主／管理員的 UI 呼叫。所有驗證均來自資料庫。"""
    if not authorized:
        raise PermissionError('此操作需官方商城管理權限')
    gid = str(guild_id)
    with transaction() as db:
        commerce._init(db)
        row = db.execute('SELECT * FROM rp_live_sale_offers WHERE guild_id=? AND id=?',
                         (gid, int(offer_id))).fetchone()
        if row is None or row['status'] == 'closed':
            raise ValueError('提案不存在或已關閉')
        product = _official_product(db, gid, row['product_ref'])
        if row['title'] != product['name'] or row['unit_price'] != product['price']:
            raise ValueError('提案標題／價格與官方商城資料不同；請建立正確提案')
        if product['scope'] != 'public':
            raise ValueError('黑卡限定商品暫時不支援直播帶貨')
        if product['stock'] is not None and row['stock_cap'] > product['stock']:
            raise ValueError('帶貨上限超過商城即時庫存')
        if row['stock_cap'] < 1 or row['unit_price'] < 1:
            raise ValueError('目前直播商品須有正整數單價和數量')
        db.execute("UPDATE rp_live_sale_offers SET status='verified' WHERE guild_id=? AND id=?", (gid, row['id']))
        return {'offer_id': int(row['id']), 'product_id': int(product['id']), 'name': str(product['name'])}


def list_verified(guild_id, session_id):
    with get_connection() as db:
        commerce._init(db)
        return [dict(r) for r in db.execute("SELECT * FROM rp_live_sale_offers WHERE guild_id=? AND session_id=? AND status='verified' ORDER BY id", (str(guild_id), int(session_id)))]
