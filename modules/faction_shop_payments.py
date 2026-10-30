陣營商店付款閘門（安全預備版）。

本版只建立審批紀錄與核准檢查，不扣款、不發貨。
真正結算須先完成商品效果發放器及經費審批政策。
不註冊 Discord 指令。
"""
from __future__ import annotations

from core.database import transaction
from modules import faction_core, faction_shop_orders


class PaymentNotReady(RuntimeError):
    """交易尚未具備安全結算條件。"""


def initialize():
    faction_shop_orders.initialize()
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_approvals (
            guild_id TEXT NOT NULL,
            order_id INTEGER NOT NULL,
            approver_character_id INTEGER NOT NULL,
            approver_owner_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,order_id,approver_character_id)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS faction_shop_approvals_order
            ON faction_shop_approvals(guild_id,order_id,decision)""")
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_payment_attempts (
            guild_id TEXT NOT NULL,
            order_id INTEGER NOT NULL,
            request_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('blocked','settled')),
            reason TEXT NOT NULL DEFAULT '',
            transaction_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,order_id),
            UNIQUE(guild_id,request_key)
        )""")


def _officer(db, gid, fid, cid):
    period = faction_core._roll(db, gid)
    if period <= 1:
        return False
    leader = db.execute("""SELECT leader_id,deputy_id FROM faction_leaders
        WHERE guild_id=? AND faction_id=? AND period=?""",
        (gid,fid,period-1)).fetchone()
    if not leader or cid not in (leader['leader_id'],leader['deputy_id']):
        return False
    member = db.execute("""SELECT 1 FROM faction_members m
        JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
        WHERE m.guild_id=? AND m.faction_id=? AND m.character_id=?
        AND c.status='active' AND c.character_type='player'""",
        (gid,fid,cid)).fetchone()
    return member is not None


def review_treasury_order(guild_id, order_id, approver_character_id,
                          approver_owner_id, decision):
    """專屬商店：已結算的前兩名可登記審核意見。

    注意：這不是正式支出授權；重大支出門檻仍待設定。
    """
    if decision not in ('approve','reject'):
        raise ValueError('審核結果不合法。')
    gid = faction_core.ensure_guild(guild_id)
    initialize()
    oid, cid = int(order_id), int(approver_character_id)
    with transaction() as db:
        order = db.execute("""SELECT * FROM faction_shop_orders
            WHERE guild_id=? AND id=?""",(gid,oid)).fetchone()
        if not order or order['status'] != 'pending_review':
            raise ValueError('訂單不存在或已不在待審狀態。')
        if order['currency'] != 'treasury':
            raise ValueError('此訂單不是共用金庫支出。')
        owner = db.execute("""SELECT owner_id FROM characters
            WHERE guild_id=? AND id=?""",(gid,cid)).fetchone()
        if not owner or str(owner['owner_id']) != str(approver_owner_id):
            raise ValueError('審核角色不屬於你。')
        if not _officer(db,gid,int(order['faction_id']),cid):
            raise ValueError('只有當期有效的第一、第二名可登記審核。')
        if cid == int(order['character_id']):
            raise ValueError('不能審核自己提出的訂單。')
        # 同一位 Discord 玩家不得透過不同角色重複核准。
        previous = db.execute("""SELECT 1 FROM faction_shop_approvals
            WHERE guild_id=? AND order_id=? AND approver_owner_id=?""",
            (gid,oid,str(approver_owner_id))).fetchone()
        if previous:
            raise ValueError('同一玩家已經審核過此訂單。')
        db.execute("""INSERT INTO faction_shop_approvals
            (guild_id,order_id,approver_character_id,approver_owner_id,decision)
            VALUES (?,?,?,?,?)""",
            (gid,oid,cid,str(approver_owner_id),decision))
        if decision == 'reject':
            db.execute("""UPDATE faction_shop_orders SET status='rejected'
                WHERE guild_id=? AND id=? AND status='pending_review'""",
                (gid,oid))
        return {'order_id':oid,'decision':decision,'charged':False}


def payment_status(guild_id, order_id):
    """顯示訂單和審批進度；不代表已獲得付款許可。"""
    initialize()
    gid = str(guild_id)
    with transaction() as db:
        row = db.execute("""SELECT * FROM faction_shop_orders
            WHERE guild_id=? AND id=?""",(gid,int(order_id))).fetchone()
        if row is None:
            raise ValueError('訂單不存在。')
        approvals = db.execute("""SELECT decision,COUNT(*) AS n
            FROM faction_shop_approvals WHERE guild_id=? AND order_id=?
            GROUP BY decision""",(gid,int(order_id))).fetchall()
        counts = {r['decision']:int(r['n']) for r in approvals}
        return {'order_id':int(row['id']), 'status':row['status'],
                'currency':row['currency'],'total_price':int(row['total_price']),
                'approvals':counts.get('approve',0),
                'rejections':counts.get('reject',0), 'charged':False,
                'settlement_enabled':False}


def settle_order(*args, **kwargs):
    """故意鎖定：避免尚無發貨與審批規則時發生不可逆扣款。"""
    raise PaymentNotReady(
        '付款尚未開放：必須先完成商品效果發放、金庫審批門檻、'
        '庫存保留及同交易對帳。')
