"""娛樂城與官方商店安全橋接。放置於 modules/casino_shop_bridge.py。

只允許後端遊戲引擎在已封存的抽獎結果上發貨；不得把此函式直接暴露給玩家。
使用舊有 shop_products / shop_inventory，不新增平行背包。
"""
from __future__ import annotations

import json
from typing import Any

from core.database import transaction
from modules.casino_service import CasinoError


def _product(conn, guild_id: str, item_id: int) -> dict[str, Any]:
    product = conn.execute(
        "SELECT * FROM shop_products WHERE guild_id=? AND id=?", (guild_id, item_id)
    ).fetchone()
    if product is None or not product['active'] or product['scope'] != 'public':
        raise CasinoError('官方商城商品不存在、下架或不允許公開抽取')
    # 受特殊管制的屬性，不允許透過抽獎獲得。
    if product['effect_stat'] in ('影響指數', 'influence_index', 'influence'):
        raise CasinoError('影響指數商品不得透過一般抽獎取得')
    return dict(product)


def validate_shop_prize(guild_id: str | int, item_id: int | str) -> dict[str, Any]:
    """由管理端在核准獎池前呼叫；對庫存不做事前保證。"""
    try:
        pid = int(item_id)
    except (TypeError, ValueError) as exc:
        raise CasinoError('商品編號不合法') from exc
    if pid <= 0:
        raise CasinoError('商品編號不合法')
    with transaction() as conn:
        return _product(conn, str(guild_id), pid)


def deliver_sealed_shop_prize(*, guild_id: str | int, round_id: int,
                              item_id: int | str, quantity: int = 1) -> dict[str, Any]:
    """將已封存的 pending 抽獎商品入背包，同事務標記 settled；重試不重複發貨。

    只接受 casino_audit 中 action='gacha_sealed' 的封存獎品，並驗證
    outcome.item_id/quantity 與呼叫參數一致。一次抽獎只支援一種商品。
    """
    if type(round_id) is not int or round_id < 1 or type(quantity) is not int or not 1 <= quantity <= 100:
        raise CasinoError('抽獎編號或數量不合法')
    try:
        pid = int(item_id)
    except (ValueError, TypeError) as exc:
        raise CasinoError('商品編號不合法') from exc
    gid = str(guild_id)
    with transaction() as conn:
        row = conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',
                           (gid, round_id)).fetchone()
        if row is None or row['game_kind'] != 'gacha':
            raise CasinoError('找不到對應的抽抽樂紀錄')
        sealed = conn.execute('''SELECT details_json FROM casino_audit
            WHERE guild_id=? AND action='gacha_sealed' AND ref_key=? ORDER BY id LIMIT 1''',
            (gid, str(round_id))).fetchone()
        if sealed is None:
            raise CasinoError('未找到已封存的獎品，禁止發貨')
        outcome = json.loads(sealed['details_json'])
        if outcome.get('type') != 'official_shop_item' or str(outcome.get('item_id')) != str(pid) or int(outcome.get('quantity', 1)) != quantity:
            raise CasinoError('發貨內容與封存結果不符')
        canonical = json.dumps(outcome, sort_keys=True, ensure_ascii=False)
        if row['status'] == 'settled':
            recorded = conn.execute('''SELECT state,quantity,reward_ref FROM casino_rewards
                WHERE guild_id=? AND round_id=? AND reward_key='official_shop_item' ''',
                (gid, round_id)).fetchone()
            if not recorded or recorded['state'] != 'delivered' or recorded['reward_ref'] != str(pid) or recorded['quantity'] != quantity:
                raise CasinoError('對局已結算，但商品紀錄不一致')
            return dict(row)
        if row['status'] != 'pending' or row['funding_mode'] != 'direct':
            raise CasinoError('此對局不可執行商品出貨')
        _product(conn, gid, pid)
        # 官方商城共用背包 API 會驗證背包容量，並在本交易中完成寫入。
        from modules.shop import adjust_inventory
        adjust_inventory(conn, gid, int(row['character_id']), pid, quantity)
        conn.execute('''INSERT INTO casino_rewards(guild_id,round_id,reward_key,reward_type,
                     reward_ref,quantity,state,delivered_at)
                     VALUES(?,?,?,?,?,?,'delivered',CURRENT_TIMESTAMP)''',
                     (gid, round_id, 'official_shop_item', 'item', str(pid), quantity))
        conn.execute('''UPDATE casino_rounds SET status='settled',outcome_json=?,reward_coins=0,
                     settled_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?''',
                     (canonical, gid, round_id))
        return dict(conn.execute('SELECT * FROM casino_rounds WHERE guild_id=? AND id=?',
                                 (gid, round_id)).fetchone())
