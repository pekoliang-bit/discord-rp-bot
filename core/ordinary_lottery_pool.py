"""普通獎池：每消耗一張普通抽獎券，就抽取並原子發放一次。

可抽取現有商城 2,000 金幣以下的有效公開商品，以及
已正式建立、開放且費用不超過 2,000 的課程。
未綁定、未正式建立的地點課程不會假裝是有效獎項。
在合格項目間暫採同等機率；未核准的額外獎品不得入池。
"""
from __future__ import annotations

import random
from core.database import get_connection, transaction
from core.ordinary_lottery_tickets import consume_in_transaction, initialize as init_tickets


class PoolNotReady(ValueError):
    pass


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_ordinary_pool_draws (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL,
        user_id TEXT NOT NULL, character_id INTEGER NOT NULL,
        prize_type TEXT NOT NULL, prize_id INTEGER NOT NULL,
        prize_name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,request_key))''')


def eligible(db,guild_id,character_id):
    gid,cid=str(guild_id),int(character_id)
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rewards=[]
    if {'shop_products','shop_inventory'}<=tables:
        items=db.execute('''SELECT id,name,price FROM shop_products
            WHERE guild_id=? AND active=1 AND scope='public' AND price BETWEEN 1 AND 2000
              AND (stock IS NULL OR stock>0) ORDER BY id''',(gid,)).fetchall()
        rewards.extend({'type':'product','id':int(r['id']),'name':str(r['name'])} for r in items)
    if {'courses','enrollments'}<=tables:
        courses=db.execute('''SELECT c.id,c.title,c.capacity FROM courses c
            WHERE c.guild_id=? AND c.status='open' AND c.fee BETWEEN 0 AND 2000
              AND NOT EXISTS(SELECT 1 FROM enrollments e WHERE e.guild_id=c.guild_id
                AND e.course_id=c.id AND e.character_id=?) ORDER BY c.id''',(gid,cid)).fetchall()
        for r in courses:
            if r['capacity'] is not None:
                count=db.execute("SELECT COUNT(*) FROM enrollments WHERE guild_id=? AND course_id=? AND status='enrolled'",(gid,int(r['id']))).fetchone()[0]
                if count>=int(r['capacity']):continue
            rewards.append({'type':'course','id':int(r['id']),'name':str(r['title'])})
    return rewards


def draw(guild_id,user_id,character_id,request_key):
    """防重複抽取：購票、扣券、發放物品／課程三者同一資料庫交易。"""
    gid,uid,cid,key=str(guild_id),str(user_id),int(character_id),str(request_key).strip()
    if not key or len(key)>150:raise ValueError('抽獎操作識別碼無效')
    with transaction() as db:
        initialize(db)
        init_tickets(db)
        prior=db.execute('SELECT * FROM rp_ordinary_pool_draws WHERE guild_id=? AND request_key=?',(gid,key)).fetchone()
        if prior:
            if str(prior['user_id'])!=uid or int(prior['character_id'])!=cid:
                raise ValueError('抽獎識別碼已被其他玩家使用')
            return dict(prior),False
        char=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",(gid,cid,uid)).fetchone()
        if not char:raise PermissionError('無權使用此角色抽獎')
        candidates=eligible(db,gid,cid)
        if not candidates:raise PoolNotReady('普通獎池暫無已核准、符合條件且可發放的獎項；抽獎券未扣除。')
        chosen=random.SystemRandom().choice(candidates)
        consume_in_transaction(db,gid,uid,cid,'ordinary:'+key)
        if chosen['type']=='product':
            from modules.task_inventory_bridge import deliver_official_item
            # 正式商城商品立即進背包，若容量不足全筆回滾，券不扣除。
            deliver_official_item(db,gid,cid,chosen['id'],1)
        else:
            row=db.execute("SELECT fee,status,capacity FROM courses WHERE guild_id=? AND id=?",(gid,chosen['id'])).fetchone()
            if not row or row['status']!='open' or not 0<=int(row['fee'])<=2000:
                raise PoolNotReady('課程資格已改變，請重新抽取；沒有消耗券。')
            if row['capacity'] is not None:
                count=db.execute("SELECT COUNT(*) FROM enrollments WHERE guild_id=? AND course_id=? AND status='enrolled'",(gid,chosen['id'])).fetchone()[0]
                if count>=int(row['capacity']):raise PoolNotReady('課程名額已滿，券未消耗。')
            db.execute('''INSERT INTO enrollments(guild_id,course_id,character_id,status,progress)
                VALUES(?,?,?,'enrolled',0)''',(gid,chosen['id'],cid))
        db.execute('''INSERT INTO rp_ordinary_pool_draws
            (guild_id,request_key,user_id,character_id,prize_type,prize_id,prize_name)
            VALUES(?,?,?,?,?,?,?)''',(gid,key,uid,cid,chosen['type'],chosen['id'],chosen['name']))
        result=db.execute('SELECT * FROM rp_ordinary_pool_draws WHERE guild_id=? AND request_key=?',(gid,key)).fetchone()
        return dict(result),True


def history(guild_id,user_id,limit=20):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('''SELECT * FROM rp_ordinary_pool_draws
            WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?''',
            (str(guild_id),str(user_id),max(1,min(100,int(limit)))))]
