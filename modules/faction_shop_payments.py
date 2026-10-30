"""陣營商店付款、雙人審核、庫存與道具發放。

只支援 effect_type='item' 的可驗證道具權益發放。
增益、暗害、防護等效果在效果執行器完成前拒絕扣款。
本模組不建立任何斜線指令。
"""
from __future__ import annotations

from core.database import transaction, get_or_create_account, account_balance
from core.economy import _payment
from modules import faction_core, faction_shop_orders

MAJOR_THRESHOLD = 100_000


class FulfillmentUnavailable(ValueError):
    pass


def initialize():
    faction_shop_orders.initialize()
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_approvals (
            guild_id TEXT NOT NULL, order_id INTEGER NOT NULL,
            approver_character_id INTEGER NOT NULL,
            approver_owner_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,order_id,approver_character_id)
        )""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS faction_shop_approval_owner
            ON faction_shop_approvals(guild_id,order_id,approver_owner_id)""")
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_settlements (
            guild_id TEXT NOT NULL, order_id INTEGER NOT NULL,
            transaction_id INTEGER, amount INTEGER NOT NULL,
            currency TEXT NOT NULL, settled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,order_id),
            UNIQUE(guild_id,transaction_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_deliveries (
            guild_id TEXT NOT NULL, order_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL, faction_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            effect_type TEXT NOT NULL, effect_key TEXT NOT NULL,
            delivered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,order_id)
        )""")
        # 舊版訂單狀態 CHECK 不允許 'settled'，故以 settlement 表為準。
        # 阻止已結算訂單被舊取消函式改成 cancelled。
        db.execute("""CREATE TRIGGER IF NOT EXISTS faction_shop_no_cancel_settled
            BEFORE UPDATE OF status ON faction_shop_orders
            WHEN EXISTS (SELECT 1 FROM faction_shop_settlements s
                WHERE s.guild_id=OLD.guild_id AND s.order_id=OLD.id)
            BEGIN SELECT RAISE(ABORT,'已結算訂單不可變更狀態'); END""")


def _officers(db, gid, fid):
    period = faction_core._roll(db, gid)
    if period <= 1:
        return {}
    row = db.execute("""SELECT leader_id,deputy_id FROM faction_leaders
        WHERE guild_id=? AND faction_id=? AND period=?""",
        (gid,fid,period-1)).fetchone()
    if not row:
        return {}
    result = {}
    for role,cid in (('leader',row['leader_id']),('deputy',row['deputy_id'])):
        if cid is None:
            continue
        owner = db.execute("""SELECT c.owner_id FROM faction_members m
            JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
            WHERE m.guild_id=? AND m.faction_id=? AND m.character_id=?
              AND c.status='active' AND c.character_type='player'""",
            (gid,fid,cid)).fetchone()
        if owner:
            result[role] = (int(cid),str(owner['owner_id']))
    return result


def _order(db,gid,oid):
    row=db.execute("SELECT * FROM faction_shop_orders WHERE guild_id=? AND id=?",
                   (gid,oid)).fetchone()
    if row is None:
        raise ValueError('訂單不存在。')
    return row


def _settled(db,gid,oid):
    return db.execute("""SELECT * FROM faction_shop_settlements
        WHERE guild_id=? AND order_id=?""",(gid,oid)).fetchone()


def _ready(db,order):
    gid,oid,fid=order['guild_id'],int(order['id']),int(order['faction_id'])
    if order['currency']=='wallet':
        return True
    if order['currency']!='treasury':
        raise ValueError('付款來源不合法。')
    officers=_officers(db,gid,fid)
    if len(officers)!=2:
        raise ValueError('第一名及第二名尚未完整就任，無法動用金庫。')
    if officers['leader'][1]==officers['deputy'][1]:
        raise ValueError('兩位幹部必須由不同玩家擔任，才能共同批准。')
    # A 方案：金庫採購申請人不得是任一審核幹部。
    if str(order['owner_id']) in {v[1] for v in officers.values()}:
        raise ValueError('金庫採購須由非審核幹部的成員提出。')
    approvals=db.execute("""SELECT approver_character_id,approver_owner_id,decision
        FROM faction_shop_approvals WHERE guild_id=? AND order_id=?""",
        (gid,oid)).fetchall()
    if any(r['decision']=='reject' for r in approvals):
        raise ValueError('訂單已被拒絕。')
    approved={(int(r['approver_character_id']),str(r['approver_owner_id']))
              for r in approvals if r['decision']=='approve'}
    # 所有金庫採購均需雙人批准，重大支出絕不例外。
    return all(v in approved for v in officers.values())


def review_treasury_order(guild_id,order_id,approver_character_id,
                          approver_owner_id,decision):
    if decision not in ('approve','reject'):
        raise ValueError('審核結果不合法。')
    gid=faction_core.ensure_guild(guild_id)
    initialize()
    oid,cid=int(order_id),int(approver_character_id)
    with transaction() as db:
        order=_order(db,gid,oid)
        if _settled(db,gid,oid) or order['status']!='pending_review':
            raise ValueError('訂單已結算或已關閉。')
        if order['currency']!='treasury':
            raise ValueError('此訂單不是金庫支出。')
        officers=_officers(db,gid,int(order['faction_id']))
        identity=(cid,str(approver_owner_id))
        if identity not in officers.values():
            raise ValueError('只有有效的第一名、第二名本人可以審核。')
        if str(order['owner_id'])==str(approver_owner_id):
            raise ValueError('不可審核自己提出的訂單。')
        if str(order['owner_id']) in {v[1] for v in officers.values()}:
            raise ValueError('依 A 方案，幹部不得提出需自己參與審核的金庫訂單。')
        if db.execute("""SELECT 1 FROM faction_shop_approvals
            WHERE guild_id=? AND order_id=? AND approver_owner_id=?""",
            (gid,oid,str(approver_owner_id))).fetchone():
            raise ValueError('同一玩家已審核過此訂單。')
        db.execute("""INSERT INTO faction_shop_approvals
            (guild_id,order_id,approver_character_id,approver_owner_id,decision)
            VALUES (?,?,?,?,?)""",(gid,oid,cid,str(approver_owner_id),decision))
        if decision=='reject':
            db.execute("""UPDATE faction_shop_orders SET status='rejected'
                WHERE guild_id=? AND id=?""",(gid,oid))
        return {'order_id':oid,'decision':decision,'charged':False,
                'approved': decision=='approve' and _ready(db,order)}


def payment_status(guild_id,order_id):
    gid=faction_core.ensure_guild(guild_id)
    initialize()
    with transaction() as db:
        order=_order(db,gid,int(order_id))
        settled=_settled(db,gid,int(order_id))
        approvals=db.execute("""SELECT decision,COUNT(*) AS n FROM faction_shop_approvals
            WHERE guild_id=? AND order_id=? GROUP BY decision""",
            (gid,int(order_id))).fetchall()
        counts={r['decision']:int(r['n']) for r in approvals}
        return {'order_id':int(order['id']),
                'status':'settled' if settled else order['status'],
                'currency':order['currency'],
                'total_price':int(order['total_price']),
                'approvals':counts.get('approve',0),
                'rejections':counts.get('reject',0),
                'charged':bool(settled),
                'major':int(order['total_price'])>=MAJOR_THRESHOLD}


def settle_order(guild_id,order_id,actor_owner_id):
    """原子結算：扣款、庫存、權益交付、金庫池及結算紀錄。

    金庫訂單由任一已核准幹部觸發；錢包訂單由買家觸發。
    僅 item 類型發放至 faction_shop_deliveries（可核對的道具權益）。
    """
    gid=faction_core.ensure_guild(guild_id)
    initialize()
    oid=int(order_id)
    with transaction() as db:
        order=_order(db,gid,oid)
        previous=_settled(db,gid,oid)
        if previous:
            return {'order_id':oid,'duplicated':True,
                    'transaction_id':previous['transaction_id'],'charged':True}
        if order['status']!='pending_review':
            raise ValueError('訂單已取消或被拒絕。')
        fid=int(order['faction_id'])
        cid=int(order['character_id'])
        member=db.execute("""SELECT 1 FROM faction_members m JOIN characters c
            ON c.guild_id=m.guild_id AND c.id=m.character_id
            WHERE m.guild_id=? AND m.faction_id=? AND m.character_id=?
              AND c.owner_id=? AND c.status='active' AND c.character_type='player'""",
            (gid,fid,cid,str(order['owner_id']))).fetchone()
        if not member:
            raise ValueError('申請角色已不屬於原陣營或已停用。')
        if order['currency']=='wallet':
            if str(actor_owner_id)!=str(order['owner_id']):
                raise ValueError('只有購買者可以結算個人訂單。')
        else:
            officers=_officers(db,gid,fid)
            if str(actor_owner_id) not in {v[1] for v in officers.values()}:
                raise ValueError('只有當期幹部可以執行金庫結算。')
        if not _ready(db,order):
            raise ValueError('尚未取得第一名及第二名共同批准。')
        item=db.execute("""SELECT * FROM faction_shop_items
            WHERE guild_id=? AND faction_id=? AND id=? AND enabled=1""",
            (gid,fid,int(order['item_id']))).fetchone()
        if not item:
            raise ValueError('商品已下架或不存在。')
        if (item['currency']!=order['currency']
            or item['effect_type']!=order['effect_type']
            or item['effect_key']!=order['effect_key']
            or int(item['effect_value'])!=int(order['effect_value'])):
            raise ValueError('商品設定已變更，請取消並重新下單。')
        if order['effect_type']!='item':
            raise FulfillmentUnavailable(
                '此效果尚未接入正式發放器，為避免扣款未發貨，禁止結算。')
        if '影響指數' in str(order['effect_key']) or str(order['effect_key']).lower()=='influence_index':
            raise ValueError('影響指數不可透過商店購買。')
        qty=int(order['quantity'])
        total=int(order['total_price'])
        if qty<=0 or total<0 or total!=qty*int(order['unit_price']):
            raise ValueError('訂單金額資料異常。')
        if total!=qty*int(item['price']):
            raise ValueError('商品價格已變更，請重新下單。')
        if item['stock'] is not None and int(item['stock'])<qty:
            raise ValueError('商品庫存不足。')
        # 零元商品不執行經濟系統正數轉帳。
        txid=None
        if total>0:
            if order['currency']=='wallet':
                sender=get_or_create_account(db,gid,'character',str(cid),'wallet')
            else:
                sender=get_or_create_account(db,gid,'organization',
                                             f'faction:{fid}','treasury')
                pool=db.execute("""SELECT pool FROM faction_groups
                    WHERE guild_id=? AND id=?""",(gid,fid)).fetchone()
                if not pool or int(pool['pool'])<total:
                    raise ValueError('陣營經費池不足。')
            receiver=get_or_create_account(db,gid,'system',
                                             'faction_shop','treasury')
            result=_payment(db,gid,f'faction:shop:settle:{gid}:{oid}',
                            sender,receiver,total,'陣營商店購買')
            if result.duplicated:
                raise ValueError('付款紀錄已存在但缺少結算資料，已停止交易。')
            txid=result.transaction_id
        if item['stock'] is not None:
            cur=db.execute("""UPDATE faction_shop_items SET stock=stock-?
                WHERE guild_id=? AND id=? AND stock>=?""",
                (qty,gid,int(item['id']),qty))
            if cur.rowcount!=1:
                raise ValueError('庫存更新失敗。')
        if order['currency']=='treasury' and total:
            cur=db.execute("""UPDATE faction_groups SET pool=pool-?
                WHERE guild_id=? AND id=? AND pool>=?""",(total,gid,fid,total))
            if cur.rowcount!=1:
                raise ValueError('陣營經費更新失敗。')
        db.execute("""INSERT INTO faction_shop_deliveries
            (guild_id,order_id,character_id,faction_id,item_id,quantity,effect_type,effect_key)
            VALUES (?,?,?,?,?,?,?,?)""",
            (gid,oid,cid,fid,int(item['id']),qty,'item',str(item['effect_key'])))
        db.execute("""INSERT INTO faction_shop_settlements
            (guild_id,order_id,transaction_id,amount,currency)
            VALUES (?,?,?,?,?)""",(gid,oid,txid,total,order['currency']))
        return {'order_id':oid,'duplicated':False,'transaction_id':txid,
                'charged':total>0,'delivered':True,'quantity':qty}
