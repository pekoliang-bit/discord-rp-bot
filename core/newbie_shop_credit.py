"""新手任務商城禮金：固定額度、不可提款、購物時原子折抵。

只允許可信任的新手獎勵呼叫 grant；購物時使用 spend_in_transaction。
不發行可兌現的金幣，也不改動商城原本的商品售價。
"""
from __future__ import annotations
from core.database import get_connection, transaction


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_newbie_shop_credit (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
        PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_newbie_shop_credit_ledger (
        guild_id TEXT NOT NULL, source_key TEXT NOT NULL,
        character_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,source_key))''')


def grant(guild_id, character_id, amount, source_key):
    if type(amount) is not int or amount <= 0:
        raise ValueError('禮金額度必須是正整數')
    gid, cid, key = str(guild_id), int(character_id), str(source_key)
    if not key or len(key)>180:
        raise ValueError('禮金來源識別碼無效')
    with transaction() as db:
        initialize(db)
        char = db.execute("SELECT 1 FROM characters WHERE guild_id=? AND id=? AND status='active'", (gid,cid)).fetchone()
        if not char: raise ValueError('角色不存在或未啟用')
        prior = db.execute('SELECT character_id,amount,reason FROM rp_newbie_shop_credit_ledger WHERE guild_id=? AND source_key=?', (gid,key)).fetchone()
        if prior:
            if int(prior['character_id'])!=cid or int(prior['amount'])!=amount or prior['reason']!='grant':
                raise ValueError('禮金來源鍵已被不同交易使用')
            return False
        db.execute('INSERT INTO rp_newbie_shop_credit_ledger (guild_id,source_key,character_id,amount,reason) VALUES(?,?,?,?,?)',
                   (gid,key,cid,amount,'grant'))
        db.execute('''INSERT INTO rp_newbie_shop_credit(guild_id,character_id,balance) VALUES(?,?,?)
            ON CONFLICT(guild_id,character_id) DO UPDATE SET balance=balance+excluded.balance''', (gid,cid,amount))
        return True


def balance(guild_id, character_id):
    with get_connection() as db:
        initialize(db)
        row=db.execute('SELECT balance FROM rp_newbie_shop_credit WHERE guild_id=? AND character_id=?',
                       (str(guild_id),int(character_id))).fetchone()
        return int(row['balance']) if row else 0


def spend_in_transaction(db, guild_id, character_id, total, source_key):
    """在商城購物的同一交易中扣禮金。避免重試消耗兩次。"""
    if type(total) is not int or total < 0: raise ValueError('商城支付金額無效')
    gid,cid,key=str(guild_id),int(character_id),str(source_key)
    initialize(db)
    prior=db.execute('SELECT character_id,amount,reason FROM rp_newbie_shop_credit_ledger WHERE guild_id=? AND source_key=?',
                     (gid,key)).fetchone()
    if prior:
        if int(prior['character_id'])!=cid or prior['reason']!='spend':
            raise ValueError('商城禮金交易來源重複')
        return -int(prior['amount'])
    row=db.execute('SELECT balance FROM rp_newbie_shop_credit WHERE guild_id=? AND character_id=?', (gid,cid)).fetchone()
    value=min(int(row['balance']),total) if row else 0
    if not value:return 0
    cur=db.execute('''UPDATE rp_newbie_shop_credit SET balance=balance-?
        WHERE guild_id=? AND character_id=? AND balance>=?''', (value,gid,cid,value))
    if cur.rowcount!=1:raise ValueError('購物禮金扣除失敗，交易已取消')
    db.execute('INSERT INTO rp_newbie_shop_credit_ledger(guild_id,source_key,character_id,amount,reason) VALUES(?,?,?,?,?)',
               (gid,key,cid,-value,'spend'))
    return value
