"""陣營商店資料核心；不提供付款或指令。"""
from __future__ import annotations

from core.database import transaction
from modules import faction_core

SHOP_TYPES = frozenset({'general', 'exclusive'})
EFFECT_TYPES = frozenset({'item', 'personal_buff', 'faction_buff', 'special_action', 'defense'})
RESTRICTED_ATTRIBUTES = frozenset({'影響指數', 'influence_index'})


def initialize():
    faction_core.initialize()
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS faction_shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            faction_id INTEGER NOT NULL,
            shop_type TEXT NOT NULL CHECK(shop_type IN ('general','exclusive')),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price INTEGER NOT NULL CHECK(price >= 0),
            currency TEXT NOT NULL CHECK(currency IN ('wallet','treasury')),
            effect_type TEXT NOT NULL,
            effect_key TEXT NOT NULL DEFAULT '',
            effect_value INTEGER NOT NULL DEFAULT 0,
            stock INTEGER CHECK(stock IS NULL OR stock >= 0),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(guild_id, faction_id) REFERENCES faction_groups(guild_id,id)
        )''')
        db.execute('''CREATE INDEX IF NOT EXISTS faction_shop_lookup
            ON faction_shop_items(guild_id,faction_id,shop_type,enabled,id)''')
        db.execute('''CREATE TABLE IF NOT EXISTS faction_shop_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')


def _validate(shop_type, currency, effect_type, effect_key, price, stock):
    if shop_type not in SHOP_TYPES:
        raise ValueError('商店類型不合法。')
    if effect_type not in EFFECT_TYPES:
        raise ValueError('商品效果類型不合法。')
    if currency not in ('wallet', 'treasury'):
        raise ValueError('付款來源不合法。')
    if shop_type == 'exclusive' and currency != 'treasury':
        raise ValueError('專屬商店只能使用陣營金庫。')
    if effect_type == 'faction_buff' and shop_type != 'exclusive':
        raise ValueError('全陣營增益須放在專屬商店。')
    if str(effect_key).strip().lower() in RESTRICTED_ATTRIBUTES or '影響指數' in str(effect_key):
        raise ValueError('影響指數不能透過商店增加。')
    if type(price) is not int or not 0 <= price <= 10**15:
        raise ValueError('價格不合法。')
    if stock is not None and (type(stock) is not int or stock < 0):
        raise ValueError('庫存不合法。')


def create_draft(guild_id, faction_id, actor_id, *, shop_type, name,
                 price, currency, effect_type, effect_key='',
                 effect_value=0, description='', stock=None):
    """由已完成管理權限驗證的上層呼叫；新商品預設停用。"""
    _validate(shop_type, currency, effect_type, effect_key, price, stock)
    if not str(name).strip() or len(str(name)) > 100:
        raise ValueError('商品名稱必須為 1 至 100 字。')
    if type(effect_value) is not int or abs(effect_value) > 10**9:
        raise ValueError('效果數值不合法。')
    if len(str(description)) > 1000:
        raise ValueError('商品說明過長。')
    gid = faction_core.ensure_guild(guild_id)
    initialize()
    with transaction() as db:
        exists = db.execute('SELECT 1 FROM faction_groups WHERE guild_id=? AND id=?',
                            (gid, int(faction_id))).fetchone()
        if not exists:
            raise ValueError('陣營不存在。')
        cur = db.execute('''INSERT INTO faction_shop_items
            (guild_id,faction_id,shop_type,name,description,price,currency,
             effect_type,effect_key,effect_value,stock,enabled)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,0)''',
            (gid,int(faction_id),shop_type,str(name).strip(),str(description),
             price,currency,effect_type,str(effect_key),effect_value,stock))
        db.execute('''INSERT INTO faction_shop_audit
            (guild_id,item_id,actor_id,action,detail) VALUES (?,?,?,?,?)''',
            (gid,cur.lastrowid,str(actor_id),'create_draft','停用待審核'))
        return cur.lastrowid


def list_items(guild_id, faction_id, shop_type, *, include_disabled=False,
               limit=20, offset=0):
    if shop_type not in SHOP_TYPES:
        raise ValueError('商店類型不合法。')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('每頁筆數不合法。')
    if type(offset) is not int or offset < 0:
        raise ValueError('分頁位置不合法。')
    gid = faction_core.ensure_guild(guild_id)
    initialize()
    with transaction() as db:
        query = '''SELECT id,name,description,price,currency,effect_type,
                   effect_key,effect_value,stock,enabled
                   FROM faction_shop_items
                   WHERE guild_id=? AND faction_id=? AND shop_type=?'''
        args = [gid,int(faction_id),shop_type]
        if not include_disabled:
            query += ' AND enabled=1'
        query += ' ORDER BY id LIMIT ? OFFSET ?'
        args.extend((limit,offset))
        return [dict(row) for row in db.execute(query, args).fetchall()]


def get_member_faction(guild_id, character_id, owner_id):
    gid = faction_core.ensure_guild(guild_id)
    with transaction() as db:
        row = db.execute('''SELECT m.faction_id FROM faction_members m
            JOIN characters c ON c.guild_id=m.guild_id AND c.id=m.character_id
            WHERE m.guild_id=? AND m.character_id=? AND c.owner_id=?
              AND c.status='active' AND c.character_type='player' ''',
            (gid,int(character_id),str(owner_id))).fetchone()
        if row is None:
            raise ValueError('角色未加入陣營，或你沒有操作權限。')
        return int(row['faction_id'])


def get_current_officers(guild_id, faction_id):
    """取得上一個已結算期別的第一、第二名；不授予空缺席次。"""
    gid = faction_core.ensure_guild(guild_id)
    with transaction() as db:
        period = faction_core._roll(db,gid)
        row = db.execute('''SELECT leader_id,deputy_id FROM faction_leaders
            WHERE guild_id=? AND faction_id=? AND period=?''',
            (gid,int(faction_id),period-1)).fetchone()
        if not row:
            return (None,None)
        officers = []
        for cid in (row['leader_id'],row['deputy_id']):
            if cid is None:
                officers.append(None)
                continue
            active = db.execute('''SELECT 1 FROM faction_members
                WHERE guild_id=? AND faction_id=? AND character_id=?''',
                (gid,int(faction_id),cid)).fetchone()
            officers.append(int(cid) if active else None)
        return tuple(officers)


def can_use_exclusive(guild_id, faction_id, character_id, owner_id):
    member_fid = get_member_faction(guild_id,character_id,owner_id)
    if member_fid != int(faction_id):
        return False
    return int(character_id) in get_current_officers(guild_id,faction_id)
