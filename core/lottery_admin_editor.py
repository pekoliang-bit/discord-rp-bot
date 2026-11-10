"""抽獎獎單與獎品管理：可編輯名稱及說明，保護已開獎歷史。"""
from __future__ import annotations

from core.database import transaction, get_connection


def initialize(db):
    for table in ('rp_lottery_lists','rp_lottery_prizes'):
        columns={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'description' not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    db.execute('''CREATE TABLE IF NOT EXISTS rp_lottery_admin_edit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
        object_kind TEXT NOT NULL,object_id INTEGER NOT NULL,
        editor_id TEXT NOT NULL,old_name TEXT NOT NULL,new_name TEXT NOT NULL,
        old_description TEXT NOT NULL,new_description TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def list_current(guild_id):
    from modules.lottery_threads import init_lottery_db
    init_lottery_db()
    with get_connection() as db:
        return [dict(r) for r in db.execute('SELECT id,name,enabled FROM rp_lottery_lists WHERE guild_id=? ORDER BY id DESC',
                                            (str(guild_id),))]


def list_prizes(guild_id,lottery_id):
    from modules.lottery_threads import init_lottery_db
    init_lottery_db()
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT p.id,p.name,p.description,p.prize_type,p.amount,p.remaining,p.unlimited
            FROM rp_lottery_prizes p JOIN rp_lottery_lists l ON l.id=p.lottery_id
            WHERE l.guild_id=? AND l.id=? ORDER BY p.id''',(str(guild_id),int(lottery_id)))]


def edit_list(guild_id, editor_id, lottery_id, *, name, description):
    from modules.lottery_threads import init_lottery_db
    init_lottery_db()
    gid=str(guild_id)
    name=str(name).strip();description=str(description).strip()
    if not 1<=len(name)<=100 or len(description)>500:
        raise ValueError('獎單名稱需1～100字；內容說明最多500字')
    with transaction() as db:
        initialize(db)
        old=db.execute('SELECT name,description FROM rp_lottery_lists WHERE guild_id=? AND id=?',
                       (gid,int(lottery_id))).fetchone()
        if not old:raise LookupError('找不到此獎單')
        # 本次只更名與修改說明，不變動歷史中獎紀錄/獎品主鍵。
        db.execute('UPDATE rp_lottery_lists SET name=?,description=? WHERE guild_id=? AND id=?',
                   (name,description,gid,int(lottery_id)))
        db.execute('''INSERT INTO rp_lottery_admin_edit_log
            (guild_id,object_kind,object_id,editor_id,old_name,new_name,old_description,new_description)
            VALUES (?,'list',?,?,?,?,?,?)''',
            (gid,int(lottery_id),str(editor_id),old['name'],name,old['description'],description))
    return name


def edit_prize(guild_id,editor_id,prize_id,*,name,description,amount=None,stock=None):
    from modules.lottery_threads import init_lottery_db
    init_lottery_db()
    gid=str(guild_id)
    name=str(name).strip();description=str(description).strip()
    if not 1<=len(name)<=100 or len(description)>500:
        raise ValueError('獎品名稱需1～100字；說明最多500字')
    if stock is not None:
        raw_stock=str(stock).strip()
        if raw_stock=='不限量':
            remaining,unlimited=0,1
        elif raw_stock.isdigit() and int(raw_stock)<=100000:
            remaining,unlimited=int(raw_stock),0
        else:
            raise ValueError('庫存請填0～100000或「不限量」')
    else:
        remaining=unlimited=None
    if amount is not None and (not str(amount).strip().isdigit() or int(amount)>100000000):
        raise ValueError('獎金必須為0～100000000金幣')
    with transaction() as db:
        initialize(db)
        old=db.execute('''SELECT p.name,p.description,p.prize_type,p.amount,p.remaining,p.unlimited,l.id AS list_id
            FROM rp_lottery_prizes p JOIN rp_lottery_lists l ON l.id=p.lottery_id
            WHERE l.guild_id=? AND p.id=?''',(gid,int(prize_id))).fetchone()
        if not old:raise LookupError('找不到此獎品')
        drawn=db.execute('SELECT 1 FROM rp_lottery_draws WHERE guild_id=? AND prize_id=? LIMIT 1',
                         (gid,int(prize_id))).fetchone()
        new_amount=int(amount) if amount is not None else int(old['amount'])
        if old['prize_type']=='gift' and new_amount!=0:
            raise ValueError('禮物獎金額必須是0；請綁定正式商城商品')
        if old['prize_type']=='money' and new_amount<=0:
            raise ValueError('金幣獎額必須大於0')
        # 中獎紀錄不可被改寫。已開獎品允許補充說明、補充未來庫存，但保留名稱與獎額。
        if drawn and (name!=old['name'] or new_amount!=int(old['amount'])):
            raise ValueError('此獎品已有中獎紀錄；名稱及獎額不可改，請建立新的獎品')
        db.execute('''UPDATE rp_lottery_prizes
            SET name=?,description=?,amount=?,remaining=?,unlimited=? WHERE id=?''',
            (name,description,new_amount,
             remaining if remaining is not None else int(old['remaining']),
             unlimited if unlimited is not None else int(old['unlimited']),int(prize_id)))
        db.execute('''INSERT INTO rp_lottery_admin_edit_log
            (guild_id,object_kind,object_id,editor_id,old_name,new_name,old_description,new_description)
            VALUES (?,'prize',?,?,?,?,?,?)''',
            (gid,int(prize_id),str(editor_id),old['name'],name,old['description'],description))
    return name
