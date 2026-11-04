"""任務官方商城商品的純資料層發貨；由 task_rewards 的交易內呼叫。

不直接引用 Discord UI 模組。商品只進入背包；穿戴／使用效果
仍應由玩家既有商城流程啟用。獎勵不扣公開銷售庫存。
"""
from __future__ import annotations


def deliver_official_item(db,guild_id,character_id,product_id,quantity):
    guild_id=str(guild_id)
    character_id=int(character_id)
    product_id=int(product_id)
    quantity=int(quantity)
    if product_id<=0 or not 1<=quantity<=2000:
        raise ValueError('任務商品編號或數量不合法')
    product=db.execute('SELECT id FROM shop_products WHERE guild_id=? AND id=?',
                       (guild_id,product_id)).fetchone()
    if not product:
        raise ValueError('商城商品不存在，請管理員先建立正式商品')
    old=db.execute('''SELECT quantity FROM shop_inventory
       WHERE guild_id=? AND character_id=? AND product_id=?''',
       (guild_id,character_id,product_id)).fetchone()
    if old is None:
        cfg=db.execute('SELECT capacity FROM shop_config WHERE guild_id=?',(guild_id,)).fetchone()
        if cfg and cfg['capacity'] is not None:
            used=db.execute('''SELECT COUNT(*) FROM shop_inventory WHERE
              guild_id=? AND character_id=? AND quantity>0''',(guild_id,character_id)).fetchone()[0]
            if used>=int(cfg['capacity']):
                raise ValueError('角色背包容量不足，發貨保留待補發')
    db.execute('''INSERT INTO shop_inventory(guild_id,character_id,product_id,quantity)
      VALUES(?,?,?,?) ON CONFLICT(guild_id,character_id,product_id)
      DO UPDATE SET quantity=shop_inventory.quantity+excluded.quantity''',
      (guild_id,character_id,product_id,quantity))
