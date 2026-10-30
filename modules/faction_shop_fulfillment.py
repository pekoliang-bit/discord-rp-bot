"""陣營商店正式背包發貨適配器。

在付款模組既有的 `with transaction() as db:` 內呼叫 deliver_in_transaction。
本模組不自行 commit、不建立 Discord 指令、不創造商城商品。
"""
from __future__ import annotations

from core.database import transaction


def initialize():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_shop_product_bindings (
            guild_id TEXT NOT NULL,
            faction_item_id INTEGER NOT NULL,
            shop_product_id INTEGER NOT NULL,
            bound_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, faction_item_id)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS faction_shop_inventory_receipts (
            guild_id TEXT NOT NULL,
            order_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            shop_product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, order_id)
        )''')


def bind_product(guild_id, faction_item_id, shop_product_id, actor_id):
    """只可由完成 `/官方` 權限檢查的管理程式呼叫。

    禁止更改已經有結算紀錄的商品綁定，避免歷史發貨混淆。
    """
    initialize()
    gid = str(guild_id)
    iid = int(faction_item_id)
    pid = int(shop_product_id)
    with transaction() as db:
        item = db.execute('''SELECT effect_type FROM faction_shop_items
            WHERE guild_id=? AND id=?''', (gid, iid)).fetchone()
        if item is None or item['effect_type'] != 'item':
            raise ValueError('僅可綁定已建立的 item 類型陣營商品。')
        product = db.execute('''SELECT effect_kind,active FROM shop_products
            WHERE guild_id=? AND id=?''', (gid, pid)).fetchone()
        if product is None or int(product['active']) != 1:
            raise ValueError('商城商品不存在或已停用。')
        if product['effect_kind'] != 'none':
            raise ValueError('有屬性效果的商品需另接正式效果系統，不可當普通道具發貨。')
        old = db.execute('''SELECT shop_product_id FROM faction_shop_product_bindings
            WHERE guild_id=? AND faction_item_id=?''', (gid, iid)).fetchone()
        if old and int(old['shop_product_id']) != pid:
            historical = db.execute('''SELECT 1 FROM faction_shop_deliveries d
                WHERE d.guild_id=? AND d.item_id=? LIMIT 1''', (gid, iid)).fetchone()
            if historical:
                raise ValueError('此陣營商品已有發貨紀錄，禁止改綁其他商品。')
        db.execute('''INSERT INTO faction_shop_product_bindings
            (guild_id,faction_item_id,shop_product_id,bound_by)
            VALUES (?,?,?,?) ON CONFLICT(guild_id,faction_item_id)
            DO UPDATE SET shop_product_id=excluded.shop_product_id,
                          bound_by=excluded.bound_by''',
            (gid, iid, pid, str(actor_id)))
        return pid


def deliver_in_transaction(db, guild_id, order_id, character_id, faction_item_id, quantity):
    """同一付款交易內發貨；若失敗，外層交易應全部回滾。

    重複訂單不會再次增加背包數量。
    必須由付款模組在驗證訂單及權限後呼叫。
    """
    gid = str(guild_id)
    oid, cid, iid, qty = map(int, (order_id, character_id, faction_item_id, quantity))
    if qty <= 0:
        raise ValueError('發貨數量不合法。')
    existing = db.execute('''SELECT character_id,shop_product_id,quantity
        FROM faction_shop_inventory_receipts WHERE guild_id=? AND order_id=?''',
        (gid, oid)).fetchone()
    if existing:
        raise ValueError('訂單已有背包發貨紀錄，禁止重複發貨。')
    binding = db.execute('''SELECT b.shop_product_id,p.effect_kind,p.active
        FROM faction_shop_product_bindings b
        JOIN shop_products p ON p.guild_id=b.guild_id AND p.id=b.shop_product_id
        WHERE b.guild_id=? AND b.faction_item_id=?''', (gid, iid)).fetchone()
    if not binding or int(binding['active']) != 1 or binding['effect_kind'] != 'none':
        raise ValueError('商品未綁定有效的無效果商城道具，禁止扣款發貨。')
    pid = int(binding['shop_product_id'])
    db.execute('''INSERT INTO shop_inventory(guild_id,character_id,product_id,quantity)
        VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id,product_id)
        DO UPDATE SET quantity=shop_inventory.quantity+excluded.quantity''',
        (gid, cid, pid, qty))
    db.execute('''INSERT INTO faction_shop_inventory_receipts
        (guild_id,order_id,character_id,shop_product_id,quantity)
        VALUES (?,?,?,?,?)''', (gid, oid, cid, pid, qty))
    return {'order_id': oid, 'product_id': pid, 'quantity': qty}
