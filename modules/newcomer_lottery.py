"""已核准新人禮包 V5.0：每角色一次、預設停用、原子且可重試的發獎。

與一般獎單隔離；只有群主明確啟用，且在「新人禮包」帖子使用 /抽獎 才可抽。
"""
from __future__ import annotations

import secrets
from core.database import transaction, get_or_create_account
from core.economy import _payment
from modules.leaderboard_points_storage import initialize as init_points

PRIZES = (("money", 25), ("free", 25), ("professional", 20), ("skill", 20), ("product", 10))
AMOUNTS = {"money": 5000, "free": 15, "professional": 15, "skill": 15, "product": 1}
LABELS = {"money": "5,000 金幣", "free": "自由點數 ×15", "professional": "專業點數 ×15", "skill": "技能點數 ×15", "product": "商城商品 ×1"}
# 同時排除非公開商城、限定與專屬商品；管理員可進一步排除指定商品編號。
BLOCKED_WORDS = ("限定", "專屬", "元老", "紀念", "管理員", "稱號", "特殊", "活動")


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_newcomer_lottery_config (
        guild_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_newcomer_lottery_exclusions (
        guild_id TEXT NOT NULL, product_id INTEGER NOT NULL,
        PRIMARY KEY(guild_id,product_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_newcomer_lottery_draws (
        guild_id TEXT NOT NULL, character_id INTEGER NOT NULL, user_id TEXT NOT NULL,
        reward_kind TEXT NOT NULL, amount INTEGER NOT NULL,
        product_id INTEGER, state TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        delivered_at TEXT,
        PRIMARY KEY(guild_id,character_id)
    )""")


def set_enabled(guild_id, enabled):
    with transaction() as db:
        initialize(db)
        db.execute("""INSERT INTO rp_newcomer_lottery_config(guild_id,enabled)
            VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
            (str(guild_id), int(bool(enabled))))


def set_product_excluded(guild_id, product_id, excluded=True):
    with transaction() as db:
        initialize(db)
        if excluded:
            db.execute('INSERT OR IGNORE INTO rp_newcomer_lottery_exclusions VALUES (?,?)', (str(guild_id),int(product_id)))
        else:
            db.execute('DELETE FROM rp_newcomer_lottery_exclusions WHERE guild_id=? AND product_id=?', (str(guild_id),int(product_id)))


def _products(db, gid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shop_products'").fetchone():
        return []
    rows = db.execute("""SELECT id,name,category,description FROM shop_products p
        WHERE p.guild_id=? AND p.active=1 AND p.scope='public'
          AND p.price BETWEEN 4000 AND 6000
          AND NOT EXISTS(SELECT 1 FROM rp_newcomer_lottery_exclusions e
            WHERE e.guild_id=p.guild_id AND e.product_id=p.id)
        ORDER BY id""", (gid,)).fetchall()
    return [r for r in rows if not any(word in ' '.join(str(r[k] or '') for k in ('name','category','description')) for word in BLOCKED_WORDS)]


def _select_reward(db, gid):
    # 固定的五種機率；商城缺商品時只替換獎品，不重新分配機率。
    ticket = secrets.randbelow(100)
    accum = 0
    kind = 'money'
    for candidate, weight in PRIZES:
        accum += weight
        if ticket < accum:
            kind = candidate
            break
    product_id = None
    if kind == 'product':
        items = _products(db, gid)
        if items:
            product_id = int(items[secrets.randbelow(len(items))]['id'])
        else:
            kind = 'money'
    return kind, (5000 if kind == 'money' else AMOUNTS[kind]), product_id


def draw(guild_id, user_id, character_id):
    """第一筆交易鎖定獎項，第二筆交易原子發獎；失敗保留原獎供重試。"""
    gid, uid, cid = str(guild_id), str(user_id), int(character_id)
    with transaction() as db:
        initialize(db)
        character = db.execute('''SELECT owner_id,status FROM characters
            WHERE guild_id=? AND id=?''', (gid,cid)).fetchone()
        if not character or character['status'] != 'active' or str(character['owner_id']) != uid:
            raise ValueError('只能使用本人已正式啟用的角色領取新人禮包。')
        previous = db.execute('''SELECT * FROM rp_newcomer_lottery_draws
            WHERE guild_id=? AND character_id=?''', (gid,cid)).fetchone()
        if previous is not None and previous['state'] == 'done':
            return {'status':'already', 'kind':previous['reward_kind'],
                    'amount':previous['amount'], 'product_id':previous['product_id']}
        enabled = db.execute('SELECT enabled FROM rp_newcomer_lottery_config WHERE guild_id=?', (gid,)).fetchone()
        if not enabled or not enabled['enabled']:
            raise ValueError('新人禮包尚未正式開放。')
        if previous is None:
            kind, amount, product_id = _select_reward(db, gid)
            db.execute('''INSERT INTO rp_newcomer_lottery_draws
                (guild_id,character_id,user_id,reward_kind,amount,product_id)
                VALUES (?,?,?,?,?,?)''', (gid,cid,uid,kind,amount,product_id))
        else:
            if previous['user_id'] != uid:
                raise ValueError('抽獎紀錄所屬玩家不符，請聯絡管理員。')
            kind, amount, product_id = previous['reward_kind'], previous['amount'], previous['product_id']
    # 抽選交易先提交 pending，發獎失敗也不會改抽或吃掉資格。
    with transaction() as db:
        current = db.execute('SELECT state,reward_kind,amount,product_id FROM rp_newcomer_lottery_draws WHERE guild_id=? AND character_id=?', (gid,cid)).fetchone()
        if current['state'] == 'done':
            return {'status':'already', 'kind':current['reward_kind'], 'amount':current['amount'], 'product_id':current['product_id']}
        kind, amount, product_id = current['reward_kind'], current['amount'], current['product_id']
        if kind == 'money':
            system = get_or_create_account(db, gid, 'system', 'issuance', 'treasury')
            receiver = get_or_create_account(db, gid, 'character', str(cid), 'wallet')
            _payment(db, gid, f'newcomer:v5:{gid}:{cid}', system, receiver, int(amount), '新人禮包 V5.0')
        elif kind in ('free','skill'):
            init_points(db)
            db.execute('''INSERT INTO lb_point_balances (guild_id,character_id,pool,balance)
                VALUES (?,?,?,?) ON CONFLICT(guild_id,character_id,pool)
                DO UPDATE SET balance=balance+excluded.balance''', (gid,cid,kind,int(amount)))
            db.execute('''INSERT INTO lb_point_history
                (guild_id,character_id,pool,amount,category,target,source_key)
                VALUES(?,?,?,?,'grant','','newcomer:v5:' || ?)''',
                (gid,cid,kind,int(amount),f'{gid}:{cid}'))
        elif kind == 'professional':
            db.execute('''CREATE TABLE IF NOT EXISTS rp_action_professional_points (
                guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
                PRIMARY KEY(guild_id,character_id))''')
            db.execute('''INSERT INTO rp_action_professional_points(guild_id,character_id,balance)
                VALUES(?,?,?) ON CONFLICT(guild_id,character_id)
                DO UPDATE SET balance=balance+excluded.balance''', (gid,cid,int(amount)))
        elif kind == 'product':
            valid = db.execute('SELECT id,name FROM shop_products WHERE guild_id=? AND id=?', (gid,product_id)).fetchone()
            if valid is None:
                raise ValueError('原中獎商品已不存在，保留原獎，請聯絡官方補發。')
            from modules.shop import adjust_inventory
            adjust_inventory(db, gid, cid, int(product_id), 1)
        else:
            raise ValueError('不支援的新人禮包獎勵。')
        db.execute('''UPDATE rp_newcomer_lottery_draws
            SET state='done', delivered_at=CURRENT_TIMESTAMP
            WHERE guild_id=? AND character_id=?''', (gid,cid))
        return {'status':'delivered','kind':kind,'amount':amount,'product_id':product_id}


def history(guild_id, user_id):
    with transaction() as db:
        initialize(db)
        return [dict(row) for row in db.execute('''SELECT character_id,reward_kind,amount,product_id,state,created_at
            FROM rp_newcomer_lottery_draws WHERE guild_id=? AND user_id=?
            ORDER BY created_at DESC''', (str(guild_id),str(user_id))).fetchall()]
