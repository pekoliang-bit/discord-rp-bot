"""角色週年生日：30個現實日一個遊戲年，與角色自行填寫的生日獨立。

每個角色每週年固定禮物一份、生日抽獎券一張；採原有角色錢包、
自由點數、免費行動值帳本。所有發放在同一 SQLite 交易完成。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta

from core.database import transaction, get_or_create_account
from core.economy import _payment
from core.birthday_config_v3 import settings

GAME_YEAR = timedelta(days=30)
GIFTS = {'coins': 50000, 'action_points': 5, 'free_points': 100, 'tickets': 1}
POOL = ((30, 'coins', 5000), (25, 'coins', 15000),
        (20, 'action_points', 5), (15, 'free_points', 15),
        (8, 'coupon', 5000), (2, 'coins', 100000))


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_character_anniversaries_v3(
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,
      activated_at TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'activation',
      starting_age INTEGER, PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_claims_v3(
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,cycle INTEGER NOT NULL,
      claimed_at TEXT NOT NULL, PRIMARY KEY(guild_id,character_id,cycle))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_tickets_v3(
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
      total INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_draws_v3(
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,draw_key TEXT NOT NULL,
      kind TEXT NOT NULL, amount INTEGER NOT NULL,
      created_at TEXT NOT NULL, PRIMARY KEY(guild_id,draw_key))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_coupons_v3(
      id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,
      character_id INTEGER NOT NULL,amount INTEGER NOT NULL,
      consumed_at TEXT,order_key TEXT UNIQUE)''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_coupon_armed_v3(
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,coupon_id INTEGER NOT NULL,
      PRIMARY KEY(guild_id,character_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_birthday_location_uses_v3(
      guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
      cycle INTEGER NOT NULL,location_key TEXT NOT NULL,
      used_at TEXT NOT NULL,PRIMARY KEY(guild_id,character_id,cycle,location_key))''')


def register_activation(db, guild_id, character_id, at=None):
    """Called inside the real activation transaction, never on character draft creation."""
    setup(db)
    row = db.execute('''SELECT age,status FROM characters WHERE guild_id=? AND id=?''',
                     (str(guild_id),int(character_id))).fetchone()
    if not row or row['status'] != 'active':
        raise ValueError('必須先正式啟用角色，才能登記系統週年')
    stamp = (at or _now()).astimezone(timezone.utc).isoformat()
    db.execute('''INSERT OR IGNORE INTO rp_character_anniversaries_v3
      (guild_id,character_id,activated_at,origin,starting_age)
      VALUES (?,?,?,?,?)''', (str(guild_id),int(character_id),stamp,'activation',row['age']))


def backfill_activation(guild_id, character_id, at, *, authorized=False):
    """Old characters: only verified records or a staff-supplied activation timestamp."""
    if not authorized:
        raise PermissionError('只有授權管理員可以補登舊角色啟用日期')
    stamp = _parse(at)
    if stamp is None or stamp > _now():
        raise ValueError('補登日期不正確')
    with transaction() as db:
        setup(db)
        row = db.execute('SELECT age,status FROM characters WHERE guild_id=? AND id=?',
                         (str(guild_id),int(character_id))).fetchone()
        if not row or row['status'] != 'active':
            raise ValueError('只允許已啟用角色補登')
        if db.execute('SELECT 1 FROM rp_character_anniversaries_v3 WHERE guild_id=? AND character_id=?',
                      (str(guild_id),int(character_id))).fetchone():
            raise ValueError('已登記的週年基準不得覆蓋')
        db.execute('''INSERT INTO rp_character_anniversaries_v3
          VALUES (?,?,?,?,?)''',(str(guild_id),int(character_id),stamp.isoformat(),'backfill',row['age']))


def status(db, guild_id, character_id, at=None):
    setup(db)
    moment = (at or _now()).astimezone(timezone.utc)
    row = db.execute('SELECT activated_at,starting_age FROM rp_character_anniversaries_v3 WHERE guild_id=? AND character_id=?',
                     (str(guild_id),int(character_id))).fetchone()
    if not row:
        return None
    origin = _parse(row['activated_at'])
    age_year = max(0, (moment-origin).total_seconds()//GAME_YEAR.total_seconds())
    cycle = int(age_year)
    cfg=settings(guild_id)
    last = origin + GAME_YEAR*cycle
    next_time = last + GAME_YEAR
    return {'cycle':cycle,'started_at':origin,'current_at':last,
            'next_at':next_time,'age':int(row['starting_age'])+cycle if row['starting_age'] is not None else None,
            'birthday_discount':cycle>=1 and moment<last+timedelta(hours=cfg['store_hours']),
            'action_discount':cycle>=1 and moment<last+timedelta(hours=cfg['action_hours'])}


def eligible_discount(db,guild_id,character_id):
    current=status(db,guild_id,character_id)
    return settings(guild_id)['store_percent_off']/100 if current and current['birthday_discount'] else 0.0


def action_price(db,guild_id,character_id):
    current=status(db,guild_id,character_id)
    return settings(guild_id)['action_price'] if current and current['action_discount'] else 500


def _grant(db,gid,cid,kind,amount,key):
    if kind=='coins':
        source=get_or_create_account(db,gid,'system','issuance','treasury')
        dest=get_or_create_account(db,gid,'character',cid,'wallet')
        _payment(db,gid,key,source,dest,int(amount),'角色生日福利')
    elif kind=='free_points':
        from modules.leaderboard_points_storage import initialize as init_points
        init_points(db)
        inserted=db.execute('''INSERT OR IGNORE INTO lb_point_history
          (guild_id,character_id,pool,amount,category,target,source_key)
          VALUES (?,?, 'free',?,'grant','',?)''',(gid,cid,int(amount),key))
        if inserted.rowcount:
            db.execute('''INSERT INTO lb_point_balances(guild_id,character_id,pool,balance)
              VALUES (?,?,'free',?) ON CONFLICT(guild_id,character_id,pool)
              DO UPDATE SET balance=balance+excluded.balance''',(gid,cid,int(amount)))
    elif kind=='action_points':
        db.execute('''CREATE TABLE IF NOT EXISTS rp_character_free_actions_v23 (
          guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
          balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
          PRIMARY KEY(guild_id,character_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS rp_character_free_action_events_v23 (
          guild_id TEXT NOT NULL,character_id INTEGER NOT NULL,
          source_key TEXT NOT NULL,delta INTEGER NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,source_key))''')
        written=db.execute('''INSERT OR IGNORE INTO rp_character_free_action_events_v23
           (guild_id,character_id,source_key,delta) VALUES (?,?,?,?)''',
           (gid,cid,key,int(amount)))
        if written.rowcount:
            db.execute('''INSERT INTO rp_character_free_actions_v23(guild_id,character_id,balance)
              VALUES (?,?,?) ON CONFLICT(guild_id,character_id)
              DO UPDATE SET balance=balance+excluded.balance''',(gid,cid,int(amount)))
    else:
        raise ValueError('未知生日獎勵類型')


def claim(guild_id,character_id,user_id):
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db)
        c=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?', (gid,cid)).fetchone()
        if not c or c['status']!='active' or str(c['owner_id'])!=str(user_id):
            raise PermissionError('只有已啟用角色的本人能領取生日禮包')
        info=status(db,gid,cid)
        if not info or info['cycle']<1:
            raise ValueError('尚未到達系統週年，或舊角色需要管理員補登')
        cycle=info['cycle']
        prior=db.execute('SELECT 1 FROM rp_birthday_claims_v3 WHERE guild_id=? AND character_id=? AND cycle=?',
                         (gid,cid,cycle)).fetchone()
        if prior:return {'status':'already','cycle':cycle}
        prefix=f'birthday:v3:{gid}:{cid}:{cycle}'
        cfg=settings(gid)
        gifts=(('coins',cfg['coins']),('action_points',cfg['free_actions']),
               ('free_points',cfg['free_points']))
        for kind,amount in gifts:
            if amount: _grant(db,gid,cid,kind,amount,prefix+':'+kind)
        if cfg['birthday_tickets']:
            db.execute('''INSERT INTO rp_birthday_tickets_v3(guild_id,character_id,total) VALUES (?,?,?)
              ON CONFLICT(guild_id,character_id) DO UPDATE SET total=total+excluded.total''',
              (gid,cid,cfg['birthday_tickets']))
        db.execute('INSERT INTO rp_birthday_claims_v3 VALUES (?,?,?,?)',
                   (gid,cid,cycle,_now().isoformat()))
        return {'status':'granted','cycle':cycle,'coins':cfg['coins'],'action_points':cfg['free_actions'],
                'free_points':cfg['free_points'],'tickets':cfg['birthday_tickets']}


def draw(guild_id,character_id,user_id,draw_key):
    gid,cid=str(guild_id),int(character_id)
    if not str(draw_key).strip():raise ValueError('抽獎識別碼不能為空')
    with transaction() as db:
        setup(db)
        c=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if not c or c['status']!='active' or str(c['owner_id'])!=str(user_id):
            raise PermissionError('角色所有權不符')
        old=db.execute('SELECT * FROM rp_birthday_draws_v3 WHERE guild_id=? AND draw_key=?',(gid,draw_key)).fetchone()
        if old:
            if int(old['character_id'])!=cid:raise PermissionError('抽獎識別碼已被其他角色使用')
            return {'kind':old['kind'],'amount':int(old['amount']),'status':'already'}
        ticket=db.execute('SELECT total FROM rp_birthday_tickets_v3 WHERE guild_id=? AND character_id=?',(gid,cid)).fetchone()
        if not ticket or int(ticket['total'])<1:raise ValueError('生日抽獎券不足')
        roll=secrets.randbelow(100)
        cursor=0
        for chance,kind,amount in POOL:
            cursor+=chance
            if roll<cursor:break
        db.execute('UPDATE rp_birthday_tickets_v3 SET total=total-1 WHERE guild_id=? AND character_id=? AND total>0',(gid,cid))
        db.execute('INSERT INTO rp_birthday_draws_v3 VALUES (?,?,?,?,?,?)',
                   (gid,cid,draw_key,kind,amount,_now().isoformat()))
        if kind=='coupon':
            db.execute('INSERT INTO rp_birthday_coupons_v3 (guild_id,character_id,amount) VALUES (?,?,?)',(gid,cid,amount))
        else:
            _grant(db,gid,cid,kind,amount,f'birthday-draw:v3:{gid}:{draw_key}')
        return {'kind':kind,'amount':amount,'status':'granted'}


def claim_location(guild_id,character_id,user_id,location_key):
    """Reserve one unique birthday location benefit per anniversary, during 72h.

    The location's business service must supply actual benefit fulfillment separately.
    """
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db)
        c=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if not c or c['status']!='active' or str(c['owner_id'])!=str(user_id):
            raise PermissionError('無權限')
        info=status(db,gid,cid)
        if not info or info['cycle']<1 or _now()>=info['current_at']+timedelta(hours=settings(gid)['location_hours']):
            raise ValueError('不在地點生日福利有效期限內')
        done=db.execute('INSERT OR IGNORE INTO rp_birthday_location_uses_v3 VALUES (?,?,?,?,?)',
                        (gid,cid,info['cycle'],str(location_key),_now().isoformat()))
        return bool(done.rowcount)


def arm_coupon(guild_id,character_id,user_id):
    # Player expressly opts into using the coupon on the next eligible purchase.
    gid,cid=str(guild_id),int(character_id)
    with transaction() as db:
        setup(db)
        owner=db.execute('SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone()
        if not owner or owner['status']!='active' or str(owner['owner_id'])!=str(user_id):
            raise PermissionError('只有角色本人可以啟用生日折價券')
        coupon=db.execute('''SELECT id FROM rp_birthday_coupons_v3
          WHERE guild_id=? AND character_id=? AND consumed_at IS NULL ORDER BY id LIMIT 1''',(gid,cid)).fetchone()
        if not coupon:raise ValueError('沒有可用的生日折價券')
        db.execute('''INSERT INTO rp_birthday_coupon_armed_v3(guild_id,character_id,coupon_id)
          VALUES (?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET coupon_id=excluded.coupon_id''',
          (gid,cid,int(coupon['id'])))
        return int(coupon['id'])


def apply_armed_coupon(db,guild_id,character_id,order_key,eligible_total,existing_discount):
    # Atomic best-of discount selection; do not consume unless more favorable.
    setup(db)
    gid,cid=str(guild_id),int(character_id)
    row=db.execute('''SELECT c.id,c.amount FROM rp_birthday_coupons_v3 c
      JOIN rp_birthday_coupon_armed_v3 a ON a.coupon_id=c.id AND a.guild_id=c.guild_id
       AND a.character_id=c.character_id
      WHERE c.guild_id=? AND c.character_id=? AND c.consumed_at IS NULL''',(gid,cid)).fetchone()
    if not row or eligible_total<=0:return 0
    saved=min(int(row['amount']),int(eligible_total))
    if saved<=int(existing_discount):return 0
    done=db.execute('''UPDATE rp_birthday_coupons_v3 SET consumed_at=?,order_key=?
      WHERE id=? AND consumed_at IS NULL''',(_now().isoformat(),order_key,int(row['id'])))
    if done.rowcount!=1:raise ValueError('生日折價券已在其他交易使用')
    db.execute('DELETE FROM rp_birthday_coupon_armed_v3 WHERE guild_id=? AND character_id=?',(gid,cid))
    return saved
