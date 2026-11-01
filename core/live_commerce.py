"""Live commerce V1: host offer catalog and interest orders, NOT payment or inventory.

To protect the existing shop ledger, offers require a trusted verified product adapter.
Without it, only a pending review listing is stored; buying/selling never alters coins.
"""
from __future__ import annotations
import time
from core.database import transaction, get_connection
from core import live_stream as live

DDL=("""CREATE TABLE IF NOT EXISTS rp_live_sale_offers (
 id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,session_id INTEGER NOT NULL,
 host_character_id INTEGER NOT NULL,product_ref TEXT NOT NULL,title TEXT NOT NULL,
 unit_price INTEGER NOT NULL,stock_cap INTEGER NOT NULL, commission_bp INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending_verification',created_at INTEGER NOT NULL,
 UNIQUE(guild_id,session_id,product_ref),
 CHECK(unit_price>=0 AND stock_cap>=0 AND commission_bp BETWEEN 0 AND 10000),
 CHECK(status IN ('pending_verification','verified','closed'))
)""", """CREATE TABLE IF NOT EXISTS rp_live_sale_interest (
 id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,offer_id INTEGER NOT NULL,
 buyer_character_id INTEGER NOT NULL,qty INTEGER NOT NULL,created_at INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'interest', request_key TEXT NOT NULL,
 UNIQUE(guild_id,request_key), CHECK(qty BETWEEN 1 AND 99)
)""",)


def _init(db):
    live._init(db)
    for sql in DDL: db.execute(sql)


def propose_offer(guild_id,owner_user_id,session_id,product_ref,title,unit_price,stock_cap,commission_bp=1000,*,now=None):
    gid,uid=str(guild_id),str(owner_user_id)
    ref=str(product_ref).strip(); title=str(title).strip()
    if not 1<=len(ref)<=100 or not 1<=len(title)<=80: raise ValueError('ååè­å¥ç¢¼æåç¨±é·åº¦ç¡æ')
    if type(unit_price) is not int or type(stock_cap) is not int or not 0<=unit_price<=10**12 or not 0<=stock_cap<=10**6: raise ValueError('å¹æ ¼æåº«å­ä¸éç¡æ')
    if type(commission_bp) is not int or not 0<=commission_bp<=10000: raise ValueError('ä½£éæ¯ä¾é ä»æ¼ 0ï½100%')
    ts=int(time.time() if now is None else now)
    with transaction() as db:
        _init(db)
        session=db.execute("SELECT * FROM rp_live_sessions WHERE guild_id=? AND id=? AND status='live'",(gid,int(session_id))).fetchone()
        if not session or session['stream_type']!='sales': raise ValueError('åªè½å¨é²è¡ä¸­çç´æ­å¸¶è²¨å ´æ¬¡æ°å¢åå')
        if session['owner_id']!=uid: raise PermissionError('åªæä¸»æç©å®¶è½æåºåå')
        return db.execute("INSERT INTO rp_live_sale_offers(guild_id,session_id,host_character_id,product_ref,title,unit_price,stock_cap,commission_bp,status,created_at) VALUES(?,?,?,?,?,?,?,?,'pending_verification',?)",(gid,int(session_id),session['host_character_id'],ref,title,unit_price,stock_cap,commission_bp,ts)).lastrowid


def verify_offer(guild_id,offer_id,verified_product,*,trusted=False):
    """Only an authorized official shop adapter may approve a live listing.

    verified_product must contain product_ref, unit_price, available_stock;
    validates caller-provided values against a trusted shop lookup (not UI text).
    """
    if not trusted: raise PermissionError('å¿é ç±å®æ¹ååå¯ä¿¡ä»»çååé©è­å¨å¼å«')
    gid=str(guild_id)
    with transaction() as db:
        _init(db)
        row=db.execute('SELECT * FROM rp_live_sale_offers WHERE guild_id=? AND id=?',(gid,int(offer_id))).fetchone()
        if not row: raise LookupError('æ¾ä¸å°å¸¶è²¨åå')
        if row['status']=='closed': raise ValueError('ååå·²éé')
        if str(verified_product['product_ref'])!=row['product_ref'] or int(verified_product['unit_price'])!=row['unit_price'] or int(verified_product['available_stock'])<row['stock_cap']:
            raise ValueError('ååååè³æãå¹æ ¼æå¯å®åº«å­ä¸ä¸è´')
        db.execute("UPDATE rp_live_sale_offers SET status='verified' WHERE guild_id=? AND id=?",(gid,int(offer_id)))
        return True


def offers(guild_id,session_id):
    with get_connection() as db:
        _init(db)
        return [dict(r) for r in db.execute('SELECT * FROM rp_live_sale_offers WHERE guild_id=? AND session_id=? ORDER BY id DESC',(str(guild_id),int(session_id))).fetchall()]


def register_interest(guild_id,owner_user_id,buyer_character_id,offer_id,qty,request_key,*,now=None):
    """Record shopping interest only; never deduct money or reserve real stock."""
    gid,uid=str(guild_id),str(owner_user_id)
    if type(qty) is not int or not 1<=qty<=99: raise ValueError('æ¸ééçº 1ï½99')
    key=str(request_key)
    with transaction() as db:
        _init(db)
        existing=db.execute('SELECT id FROM rp_live_sale_interest WHERE guild_id=? AND request_key=?',(gid,key)).fetchone()
        if existing: return int(existing['id'])
        buyer=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND owner_id=? AND status='active'",(gid,int(buyer_character_id),uid)).fetchone()
        if not buyer: raise PermissionError('åªè½æ¿èªå·±å·²åç¨è§è²ç»è¨è³¼è²·æå')
        row=db.execute("SELECT o.*,s.status AS session_status FROM rp_live_sale_offers o JOIN rp_live_sessions s ON s.guild_id=o.guild_id AND s.id=o.session_id WHERE o.guild_id=? AND o.id=?",(gid,int(offer_id))).fetchone()
        if not row or row['status']!='verified' or row['session_status']!='live': raise ValueError('ååå°æªééå®æ¹é©è­æç´æ­å·²çµæ')
        return db.execute("INSERT INTO rp_live_sale_interest(guild_id,offer_id,buyer_character_id,qty,created_at,request_key) VALUES(?,?,?,?,?,?)",(gid,int(offer_id),int(buyer_character_id),qty,int(time.time() if now is None else now),key)).lastrowid
