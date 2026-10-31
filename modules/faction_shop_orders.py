"""陣營商店訂單與審批核心（不註冊額外斜線指令）。

這個版本刻意不扣款：在商品發放器、支出核准規則和金庫對帳
完成前，所有訂單都停在 pending_review，避免扣款後無法交貨。
"""
from __future__ import annotations

from core.database import transaction
from modules import faction_core, faction_shop

MAX_REQUEST_ID = 100


def initialize():
    faction_shop.initialize()
    with transaction() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS faction_shop_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            faction_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            character_id INTEGER NOT NULL,
            owner_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            unit_price INTEGER NOT NULL CHECK(unit_price >= 0),
            total_price INTEGER NOT NULL CHECK(total_price >= 0),
            currency TEXT NOT NULL CHECK(currency IN ('wallet','treasury')),
            effect_type TEXT NOT NULL,
            effect_key TEXT NOT NULL,
            effect_value INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review'
                CHECK(status IN ('pending_review','cancelled','rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id,request_key)
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS faction_shop_orders_member
            ON faction_shop_orders(guild_id,character_id,created_at)""")


def _officer_in_db(db, gid, fid, cid):
    period = faction_core._roll(db, gid)
    if period <= 1:
        return False
    row = db.execute("""SELECT leader_id,deputy_id FROM faction_leaders
        WHERE guild_id=? AND faction_id=? AND period=?""",
        (gid, fid, period - 1)).fetchone()
    return bool(row and cid in (row['leader_id'], row['deputy_id']))


def request_purchase(guild_id, character_id, owner_id, item_id,
                     quantity, request_id, *, proposal_only=False):
    """建立待審訂單；不扣金幣、不減庫存、不發放效果。"""
    if type(quantity) is not int or not 1 <= quantity <= 99:
        raise ValueError('購買數量必須介於 1 至 99。')
    request_id = str(request_id).strip()
    if not request_id or len(request_id) > MAX_REQUEST_ID:
        raise ValueError('請求編號不合法。')
    gid = faction_core.ensure_guild(guild_id)
    initialize()
    cid, iid = int(character_id), int(item_id)
    key = f'faction:shop:{gid}:{request_id}'
    with transaction() as db:
        existing = db.execute("""SELECT * FROM faction_shop_orders
            WHERE guild_id=? AND request_key=?""", (gid,key)).fetchone()
        if existing:
            if (int(existing['character_id']) != cid or
                int(existing['item_id']) != iid or
                int(existing['quantity']) != quantity or
                str(existing['owner_id']) != str(owner_id)):
                raise ValueError('此請求編號已用於其他訂單。')
            return dict(existing)
        member = db.execute("""SELECT m.faction_id FROM faction_members m
            JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
            WHERE m.guild_id=? AND m.character_id=? AND c.owner_id=?
              AND c.status='active' AND c.character_type='player'""",
            (gid,cid,str(owner_id))).fetchone()
        if not member:
            raise ValueError('只能以自己的已啟用陣營角色購買。')
        fid = int(member['faction_id'])
        item = db.execute("""SELECT * FROM faction_shop_items
            WHERE guild_id=? AND faction_id=? AND id=? AND enabled=1""",
            (gid,fid,iid)).fetchone()
        if not item:
            raise ValueError('商品不存在、未上架或不屬於目前陣營。')
        if item['stock'] is not None and int(item['stock']) < quantity:
            raise ValueError('商品目前庫存不足。')
        # A 方案：一般成員可以提出金庫採購提案，
        # 但必須由第一、第二名共同批准後才能付款。
        # 提案不等於取得專屬商店管理或購買權限。
        # 專屬商店只允許當期老大或副手提出採購；此處不代表核准金庫支出。
        if item['shop_type'] == 'exclusive':
            # 一般成員可以提議使用金庫，但不能直接購買或批准付款。
            # 由老大與副手共同審核，並由幹部執行正式結算。
            if not proposal_only:
                raise ValueError('專屬商店請使用金庫採購提案，不能直接購買。')
            period = faction_core._roll(db, gid)
            officers = db.execute('''SELECT c.owner_id FROM faction_leaders l
                JOIN characters c ON c.guild_id=l.guild_id
                  AND c.id IN (l.leader_id,l.deputy_id)
                WHERE l.guild_id=? AND l.faction_id=? AND l.period=?''',
                (gid, fid, period - 1)).fetchall() if period > 1 else []
            if str(owner_id) in {str(r['owner_id']) for r in officers}:
                raise ValueError('幹部本人或其其他角色不可提出需由本人審核的金庫提案。')
        elif proposal_only:
            raise ValueError('一般商店不需要金庫採購提案。')
        if item['shop_type'] == 'exclusive' and item['currency'] != 'treasury':
            raise ValueError('專屬商店付款來源不合法。')
        if '影響指數' in str(item['effect_key']) or str(item['effect_key']).lower() == 'influence_index':
            raise ValueError('影響指數不可由商店購買。')
        price = int(item['price'])
        total = price * quantity
        if total > 10**15:
            raise ValueError('訂單總額超出限制。')
        cur = db.execute("""INSERT INTO faction_shop_orders
            (guild_id,request_key,faction_id,item_id,character_id,owner_id,
             quantity,unit_price,total_price,currency,effect_type,effect_key,effect_value)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (gid,key,fid,iid,cid,str(owner_id),quantity,price,total,
             item['currency'],item['effect_type'],item['effect_key'],item['effect_value']))
        return dict(db.execute('SELECT * FROM faction_shop_orders WHERE id=?',
                               (cur.lastrowid,)).fetchone())


def cancel_request(guild_id, order_id, owner_id):
    """訂單擁有者可取消待審訂單。"""
    initialize()
    with transaction() as db:
        cur = db.execute("""UPDATE faction_shop_orders SET status='cancelled'
            WHERE guild_id=? AND id=? AND owner_id=? AND status='pending_review'""",
            (str(guild_id),int(order_id),str(owner_id)))
        if cur.rowcount != 1:
            raise ValueError('訂單不存在、非本人所有，或已不允許取消。')
        return True


def list_my_orders(guild_id, owner_id, limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('查詢筆數不合法。')
    initialize()
    with transaction() as db:
        return [dict(r) for r in db.execute("""SELECT * FROM faction_shop_orders
            WHERE guild_id=? AND owner_id=? ORDER BY id DESC LIMIT ?""",
            (str(guild_id),str(owner_id),limit)).fetchall()]
