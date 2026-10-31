"""陣營供奉：扣款、入池、積分、防重複紀錄同一交易。"""

from __future__ import annotations

from core.database import transaction, get_or_create_account
from core.economy import _payment, EconomyError
from modules import faction_core


def _initialize(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS faction_payment_links (
            guild_id TEXT NOT NULL,
            request_key TEXT NOT NULL,
            faction_ledger_id INTEGER NOT NULL,
            economy_transaction_id INTEGER NOT NULL,
            PRIMARY KEY(guild_id, request_key),
            UNIQUE(guild_id, economy_transaction_id),
            UNIQUE(guild_id, faction_ledger_id)
        )
    """)


def donate(guild_id, character_id, user_id, amount, request_id):
    if type(amount) is not int or not 1 <= amount <= 10**15:
        raise ValueError("供奉金額必須是合法正整數。")

    request_id = str(request_id).strip()
    if not request_id or len(request_id) > 100:
        raise ValueError("交易編號不合法。")

    gid = faction_core.ensure_guild(guild_id)
    key = f"faction:offering:{gid}:{request_id}"

    if len(key) > 200:
        raise ValueError("交易編號過長。")

    cid = int(character_id)

    with transaction() as db:
        _initialize(db)

        # 優先檢查重複請求，避免重複扣款。
        previous = db.execute("""
            SELECT
                l.faction_id,
                l.character_id,
                l.amount,
                l.points
            FROM faction_payment_links p
            JOIN faction_ledger l
              ON l.id = p.faction_ledger_id
            WHERE p.guild_id = ?
              AND p.request_key = ?
        """, (gid, key)).fetchone()

        if previous:
            if (
                int(previous["character_id"]) != cid
                or int(previous["amount"]) != amount
            ):
                raise ValueError("交易編號已被其他供奉使用。")

            return {
                "duplicated": True,
                "amount": amount,
                "points": int(previous["points"]),
            }

        # 驗證角色身分。
        character = db.execute("""
            SELECT owner_id, status, character_type
            FROM characters
            WHERE guild_id = ? AND id = ?
        """, (gid, cid)).fetchone()

        if (
            not character
            or str(character["owner_id"]) != str(user_id)
            or character["status"] != "active"
            or character["character_type"] != "player"
        ):
            raise ValueError("只能以自己已啟用的原創角色供奉。")

        member = db.execute("""
            SELECT faction_id
            FROM faction_members
            WHERE guild_id = ? AND character_id = ?
        """, (gid, cid)).fetchone()

        if not member:
            raise ValueError("角色尚未加入陣營。")

        fid = int(member["faction_id"])

        # 使用原本的角色錢包及組織金庫。
        sender = get_or_create_account(
            db, gid, "character", str(cid), "wallet"
        )
        receiver = get_or_create_account(
            db, gid, "organization",
            f"faction:{fid}", "treasury"
        )

        payment = _payment(
            db, gid, key, sender, receiver,
            amount, "陣營供奉"
        )

        if payment.duplicated:
            raise EconomyError(
                "付款已存在但缺少陣營流水，"
                "已停止入帳，請管理員核對。"
            )

        # 計算本期有效個人貢獻。
        period = faction_core._roll(db, gid)

        row = db.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM faction_ledger
            WHERE guild_id = ?
              AND faction_id = ?
              AND character_id = ?
              AND period = ?
              AND source IN (
                  'offering',
                  'commission',
                  'personal_award',
                  'sponsor_personal'
              )
        """, (gid, fid, cid, period)).fetchone()

        before = int(row["total"])

        # 小額供奉可以累計滿 100 金幣換算積分。
        points = (
            (before + amount) // 100
            - before // 100
        )

        # 更新陣營經費池。
        updated = db.execute("""
            UPDATE faction_groups
            SET pool = pool + ?
            WHERE guild_id = ? AND id = ?
        """, (amount, gid, fid))

        if updated.rowcount != 1:
            raise ValueError("陣營不存在，供奉已取消。")

        # 建立陣營流水。
        cursor = db.execute("""
            INSERT INTO faction_ledger (
                guild_id,
                faction_id,
                character_id,
                source,
                amount,
                points,
                period,
                memo
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            gid, fid, cid, "offering",
            amount, points, period, key
        ))

        # 綁定經濟系統交易與陣營流水。
        db.execute("""
            INSERT INTO faction_payment_links (
                guild_id,
                request_key,
                faction_ledger_id,
                economy_transaction_id
            )
            VALUES (?, ?, ?, ?)
        """, (
            gid,
            key,
            cursor.lastrowid,
            payment.transaction_id,
        ))

        return {
            "duplicated": False,
            "amount": amount,
            "points": points,
        }


# 僅普通（沒有任何效果）的持有型道具可供奉；物品折算為陣營經費。
def item_options(guild_id, character_id, user_id, limit=25, offset=0):
    gid=faction_core.ensure_guild(guild_id)
    cid=int(character_id)
    with transaction() as db:
        _validate_donor(db,gid,cid,user_id)
        return db.execute("""SELECT i.product_id,p.name,p.price,i.quantity
            FROM shop_inventory i JOIN shop_products p
              ON p.guild_id=i.guild_id AND p.id=i.product_id
            WHERE i.guild_id=? AND i.character_id=? AND i.quantity>0
              AND p.active=1 AND p.effect_kind='none' AND p.price>0
            ORDER BY i.product_id LIMIT ? OFFSET ?""",
            (gid,cid,min(max(int(limit),1),25),max(0,int(offset)))).fetchall()


def _validate_donor(db,gid,cid,user_id):
    character=db.execute("""SELECT owner_id,status,character_type FROM characters
        WHERE guild_id=? AND id=?""",(gid,cid)).fetchone()
    if (not character or str(character['owner_id'])!=str(user_id)
        or character['status']!='active' or character['character_type']!='player'):
        raise ValueError('只能使用本人已啟用的原創角色供奉。')
    membership=db.execute("""SELECT faction_id FROM faction_members
        WHERE guild_id=? AND character_id=?""",(gid,cid)).fetchone()
    if not membership: raise ValueError('角色尚未加入陣營。')
    return int(membership['faction_id'])


def donate_item(guild_id, character_id, user_id, product_id, quantity, request_id):
    gid=faction_core.ensure_guild(guild_id)
    cid,pid,qty=int(character_id),int(product_id),int(quantity)
    if qty<1 or qty>100000: raise ValueError('供奉數量不合法。')
    request_id=str(request_id).strip()
    if not request_id or len(request_id)>100: raise ValueError('交易編號不合法。')
    key=f'faction:item-offering:{gid}:{request_id}'
    with transaction() as db:
        _initialize(db)
        db.execute("""CREATE TABLE IF NOT EXISTS faction_item_donations(
            guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
            character_id INTEGER NOT NULL, faction_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL, total_value INTEGER NOT NULL,
            points INTEGER NOT NULL, ledger_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id,request_key))""")
        previous=db.execute('SELECT * FROM faction_item_donations WHERE guild_id=? AND request_key=?',
                            (gid,key)).fetchone()
        if previous:
            if (int(previous['character_id'])!=cid or int(previous['product_id'])!=pid
                or int(previous['quantity'])!=qty):
                raise ValueError('交易編號已被其他供奉使用。')
            return {'duplicated':True,'amount':int(previous['total_value']),
                    'points':int(previous['points']),'quantity':qty}
        fid=_validate_donor(db,gid,cid,user_id)
        item=db.execute("""SELECT p.price,p.effect_kind,p.active,i.quantity
            FROM shop_inventory i JOIN shop_products p
              ON p.guild_id=i.guild_id AND p.id=i.product_id
            WHERE i.guild_id=? AND i.character_id=? AND i.product_id=?""",
            (gid,cid,pid)).fetchone()
        if (not item or not int(item['active']) or item['effect_kind']!='none'
            or int(item['price'])<=0 or int(item['quantity'])<qty):
            raise ValueError('物品不存在、數量不足，或此物品不能供奉。')
        price=int(item['price'])
        value=price*qty
        if value>10**15: raise ValueError('折算金額過大。')
        period=faction_core._roll(db,gid)
        row=db.execute("""SELECT COALESCE(SUM(amount),0) AS total FROM faction_ledger
          WHERE guild_id=? AND faction_id=? AND character_id=? AND period=?
          AND source IN ('offering','commission','personal_award','sponsor_personal')""",
          (gid,fid,cid,period)).fetchone()
        before=int(row['total'])
        points=(before+value)//100-before//100
        deducted=db.execute("""UPDATE shop_inventory SET quantity=quantity-?
          WHERE guild_id=? AND character_id=? AND product_id=? AND quantity>=?""",
          (qty,gid,cid,pid,qty))
        if deducted.rowcount!=1: raise ValueError('背包數量變動，請重新確認。')
        db.execute("""DELETE FROM shop_inventory WHERE guild_id=? AND character_id=?
          AND product_id=? AND quantity=0""",(gid,cid,pid))
        updated=db.execute("""UPDATE faction_groups SET pool=pool+?
          WHERE guild_id=? AND id=?""",(value,gid,fid))
        if updated.rowcount!=1: raise ValueError('陣營不存在。')
        ledger=db.execute("""INSERT INTO faction_ledger
          (guild_id,faction_id,character_id,source,amount,points,period,memo)
          VALUES (?,?,?,?,?,?,?,?)""",
          (gid,fid,cid,'offering',value,points,period,
           f'物品供奉 product={pid} quantity={qty} request={key}'))
        db.execute("""INSERT INTO faction_item_donations
          (guild_id,request_key,character_id,faction_id,product_id,quantity,
           unit_price,total_value,points,ledger_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
           (gid,key,cid,fid,pid,qty,price,value,points,ledger.lastrowid))
        return {'duplicated':False,'amount':value,'points':points,'quantity':qty}
