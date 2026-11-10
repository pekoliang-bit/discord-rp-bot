"""普通抽獎券帳本：每張800金幣、每玩家每週最多買5張。

這個模組本身不建立抽獎獎池，也不擅自選取或發放獎品。
抽獎處理器必須在同一個 transaction() 內扣券及確定獎品，以便失敗回滾。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.database import get_connection, transaction, get_or_create_account
from core.economy import _payment

TICKET_NAME = '普通抽獎券'
POOL_NAME = '普通獎池'
PRICE = 800
WEEKLY_LIMIT = 5


def week_key(now=None):
    local = (now or datetime.now(ZoneInfo('Asia/Taipei'))).astimezone(ZoneInfo('Asia/Taipei'))
    monday = local.date() - timedelta(days=local.weekday())
    return monday.isoformat()


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_ordinary_ticket_settings (
        guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
        price INTEGER NOT NULL DEFAULT 800, weekly_limit INTEGER NOT NULL DEFAULT 5,
        created_by TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_ordinary_ticket_balances (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
        PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_ordinary_ticket_events (
        guild_id TEXT NOT NULL, event_key TEXT NOT NULL,
        character_id INTEGER NOT NULL, user_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('purchase','newbie','draw','admin')),
        quantity INTEGER NOT NULL, week_key TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,event_key))''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_ordinary_ticket_week
        ON rp_ordinary_ticket_events(guild_id,user_id,kind,week_key)''')


def configure(guild_id, admin_id):
    """群主核准普通券固定價及週限後正式上架特殊商店。"""
    gid=str(guild_id)
    with transaction() as db:
        initialize(db)
        db.execute('''INSERT INTO rp_ordinary_ticket_settings(guild_id,enabled,price,weekly_limit,created_by)
            VALUES (?,1,800,5,?) ON CONFLICT(guild_id)
            DO UPDATE SET enabled=1,price=800,weekly_limit=5,
              updated_at=CURRENT_TIMESTAMP''',(gid,str(admin_id)))
    return True


def enabled(guild_id):
    with get_connection() as db:
        initialize(db)
        row=db.execute('SELECT enabled FROM rp_ordinary_ticket_settings WHERE guild_id=?',
                       (str(guild_id),)).fetchone()
        return bool(row and row['enabled'])


def _character(db, guild_id, user_id, character_id):
    row = db.execute('''SELECT 1 FROM characters WHERE guild_id=? AND id=?
        AND owner_id=? AND status='active' ''',
        (str(guild_id), int(character_id), str(user_id))).fetchone()
    if row is None:
        raise PermissionError('角色不是你的已啟用角色')


def _apply(db, guild_id, user_id, character_id, key, kind, quantity, weekly=None):
    gid, uid, cid, key = str(guild_id), str(user_id), int(character_id), str(key)
    if not key or len(key) > 180 or type(quantity) is not int or not quantity:
        raise ValueError('抽獎券數量或事件識別碼無效')
    initialize(db)
    _character(db, gid, uid, cid)
    existing = db.execute('SELECT * FROM rp_ordinary_ticket_events WHERE guild_id=? AND event_key=?', (gid, key)).fetchone()
    if existing:
        if (int(existing['character_id']) != cid or str(existing['user_id']) != uid
                or existing['kind'] != kind or int(existing['quantity']) != quantity):
            raise ValueError('抽獎券事件識別碼已被其他交易使用')
        return False
    if quantity < 0:
        have = db.execute('SELECT balance FROM rp_ordinary_ticket_balances WHERE guild_id=? AND character_id=?',
                          (gid, cid)).fetchone()
        if have is None or int(have['balance']) < -quantity:
            raise ValueError('普通抽獎券不足')
    db.execute('''INSERT INTO rp_ordinary_ticket_events
        (guild_id,event_key,character_id,user_id,kind,quantity,week_key)
        VALUES(?,?,?,?,?,?,?)''', (gid, key, cid, uid, kind, quantity, weekly))
    if quantity < 0:
        updated = db.execute('''UPDATE rp_ordinary_ticket_balances SET balance=balance+?
            WHERE guild_id=? AND character_id=? AND balance>=?''',
            (quantity, gid, cid, -quantity))
        if updated.rowcount != 1:
            raise ValueError('普通抽獎券扣除失敗，交易已回滾')
    else:
        db.execute('''INSERT INTO rp_ordinary_ticket_balances (guild_id,character_id,balance)
            VALUES(?,?,?) ON CONFLICT(guild_id,character_id)
            DO UPDATE SET balance=balance+excluded.balance''', (gid, cid, quantity))
    return True


def purchase(guild_id, user_id, character_id, request_key, quantity=1, *, now=None):
    if type(quantity) is not int or not 1 <= quantity <= WEEKLY_LIMIT:
        raise ValueError('每次只能購買1～5張普通抽獎券')
    gid, uid, cid = str(guild_id), str(user_id), int(character_id)
    key = 'purchase:' + str(request_key).strip()
    week = week_key(now)
    with transaction() as db:
        initialize(db)
        _character(db, gid, uid, cid)
        settings = db.execute('SELECT enabled,price,weekly_limit FROM rp_ordinary_ticket_settings WHERE guild_id=?', (gid,)).fetchone()
        if not settings or not settings['enabled']:
            raise ValueError('普通抽獎券尚未由群主在抽獎管理中心正式上架')
        previous = db.execute('SELECT * FROM rp_ordinary_ticket_events WHERE guild_id=? AND event_key=?',
                              (gid, key)).fetchone()
        if previous:
            if (previous['kind'] != 'purchase' or previous['user_id'] != uid
                    or int(previous['character_id']) != cid or int(previous['quantity']) != quantity):
                raise ValueError('購買識別碼與先前交易不同')
            return False
        current = db.execute('''SELECT COALESCE(SUM(quantity),0) AS n FROM rp_ordinary_ticket_events
            WHERE guild_id=? AND user_id=? AND kind='purchase' AND week_key=?''',
            (gid, uid, week)).fetchone()['n']
        if int(current) + quantity > WEEKLY_LIMIT:
            raise ValueError(f'本週每名玩家最多購買 {WEEKLY_LIMIT} 張普通抽獎券')
        src = get_or_create_account(db, gid, 'character', str(cid), 'wallet')
        dst = get_or_create_account(db, gid, 'system', 'ordinary_lottery_sales', 'treasury')
        _payment(db, gid, 'ordinary_ticket:' + key, src, dst, PRICE * quantity, '購買普通抽獎券')
        return _apply(db, gid, uid, cid, key, 'purchase', quantity, week)


def grant_newbie(guild_id, user_id, character_id, source_key, quantity=1):
    with transaction() as db:
        return _apply(db, guild_id, user_id, character_id, 'newbie:' + str(source_key),
                      'newbie', int(quantity))


def balance(guild_id, character_id):
    with get_connection() as db:
        initialize(db)
        row = db.execute('SELECT balance FROM rp_ordinary_ticket_balances WHERE guild_id=? AND character_id=?',
                         (str(guild_id), int(character_id))).fetchone()
        return int(row['balance']) if row else 0


def consume_in_transaction(db, guild_id, user_id, character_id, draw_key):
    """抽獎決定獎品、保存結果與扣券必須共用呼叫者的交易。"""
    return _apply(db, guild_id, user_id, character_id, 'draw:' + str(draw_key), 'draw', -1)
