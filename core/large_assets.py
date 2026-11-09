"""大型資產 V1.4：同群隔離、產權、交易、租賃、維護及拍賣。

僅此模組建立 rp_asset_* 資料表。所有金錢通過既有雙分錄 _payment，
重要操作同一 BEGIN IMMEDIATE 完成。銀行貸款不在此建立第二套帳本。
"""
from __future__ import annotations
import csv
import json
import math
import time
import uuid
from pathlib import Path
from core.database import transaction, get_connection, get_or_create_account, account_balance
from core.economy import _payment

CATALOG_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'large_assets' / 'catalog.csv'
RENT_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'large_assets' / 'rent.csv'
LUXURY_CARS = {'CAR-06', 'CAR-08', 'CAR-09', 'CAR-10'}
LUXURY_HOMES = {'EST-07', 'EST-08', 'EST-09', 'EST-10'}
HOUSING_CAPACITY = {'EST-01':2,'EST-02':2,'EST-03':3,'EST-04':5,'EST-05':7,'EST-06':10,'EST-07':10,'EST-08':15,'EST-09':30,'EST-10':50}
TERM_DAYS = {7,14,28}
AUCTION_HOURS = {6,24,72}

class AssetError(ValueError):
    pass

def now(): return int(time.time())
def gid(value): return str(value)
def _positive(value, label='金額'):
    try: number = int(value)
    except (TypeError,ValueError): raise AssetError(f'{label}必須是正整數')
    if number <= 0 or number > 10**12: raise AssetError(f'{label}必須介於 1 至一兆')
    return number

def initialize(db):
    ddl = [
    '''CREATE TABLE IF NOT EXISTS rp_asset_templates (
      code TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
      base_price INTEGER NOT NULL DEFAULT 0, luxury INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1, min_price_only INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, template_code TEXT NOT NULL,
      label TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', unique_location_key TEXT,
      parent_id INTEGER, base_value INTEGER NOT NULL, appraised_value INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'active', origin TEXT NOT NULL DEFAULT 'purchase',
      created_at INTEGER NOT NULL, last_value_at INTEGER NOT NULL,
      FOREIGN KEY(template_code) REFERENCES rp_asset_templates(code),
      UNIQUE (guild_id,id), UNIQUE(guild_id,unique_location_key))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_shares (
      guild_id TEXT NOT NULL, asset_id INTEGER NOT NULL,
      holder_type TEXT NOT NULL CHECK(holder_type IN ('character','organization')),
      holder_id TEXT NOT NULL, basis_points INTEGER NOT NULL CHECK(basis_points BETWEEN 1 AND 10000),
      PRIMARY KEY(guild_id,asset_id,holder_type,holder_id))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_orders (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, asset_id INTEGER NOT NULL,
      seller_type TEXT NOT NULL, seller_id TEXT NOT NULL, price INTEGER NOT NULL,
      mode TEXT NOT NULL CHECK(mode IN ('sale','auction','gift','rent')),
      status TEXT NOT NULL DEFAULT 'open', buyer_type TEXT, buyer_id TEXT,
      created_at INTEGER NOT NULL, ends_at INTEGER, highest_bid INTEGER NOT NULL DEFAULT 0,
      highest_bidder TEXT, extension_seconds INTEGER NOT NULL DEFAULT 0, completed_at INTEGER,
      UNIQUE(guild_id,id))''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS rp_asset_one_open_order
      ON rp_asset_orders(guild_id,asset_id) WHERE status='open' ''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_bids (
      guild_id TEXT NOT NULL, order_id INTEGER NOT NULL, bidder_id TEXT NOT NULL,
      amount INTEGER NOT NULL, placed_at INTEGER NOT NULL,
      PRIMARY KEY(guild_id,order_id,bidder_id,placed_at))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_leases (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, asset_id INTEGER NOT NULL,
      tenant_id INTEGER NOT NULL, landlord_type TEXT NOT NULL, landlord_id TEXT NOT NULL,
      period_rent INTEGER NOT NULL, deposit INTEGER NOT NULL, start_at INTEGER NOT NULL,
      ends_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active',
      UNIQUE(guild_id,id))''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS rp_asset_one_active_lease ON rp_asset_leases(guild_id,asset_id) WHERE status='active' ''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_dues (
      guild_id TEXT NOT NULL,asset_id INTEGER NOT NULL,period_start INTEGER NOT NULL,
      due_amount INTEGER NOT NULL,paid_amount INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL, PRIMARY KEY(guild_id,asset_id,period_start))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_lien (
      guild_id TEXT NOT NULL,asset_id INTEGER NOT NULL,bank_reference TEXT NOT NULL,
      outstanding_amount INTEGER NOT NULL CHECK(outstanding_amount>=0),status TEXT NOT NULL DEFAULT 'active',
      PRIMARY KEY(guild_id,asset_id,bank_reference))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, asset_id INTEGER,
      event_key TEXT NOT NULL,kind TEXT NOT NULL, actor_id TEXT NOT NULL,
      payload TEXT NOT NULL DEFAULT '{}',created_at INTEGER NOT NULL,
      UNIQUE(guild_id,event_key))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_homes (
      guild_id TEXT NOT NULL, character_id INTEGER NOT NULL,asset_id INTEGER NOT NULL,
      PRIMARY KEY(guild_id,character_id))''',
    '''CREATE TABLE IF NOT EXISTS rp_asset_lease_prices (
      name TEXT PRIMARY KEY, weekly_rent INTEGER NOT NULL, deposit INTEGER NOT NULL)'''
    ]
    ddl.append('''CREATE TABLE IF NOT EXISTS rp_asset_template_overrides (
      guild_id TEXT NOT NULL, code TEXT NOT NULL, category TEXT NOT NULL,
      name TEXT NOT NULL, base_price INTEGER NOT NULL, luxury INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1,min_price_only INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild_id,code))''')
    for statement in ddl: db.execute(statement)
    with CATALOG_PATH.open(encoding='utf-8-sig',newline='') as fp:
        for item in csv.DictReader(fp):
            code=item['商品模板代碼']; value=item['官方基準價(金幣)'].replace(',','')
            digits=''.join(c for c in value if c.isdigit())
            price=int(digits) if digits else 0
            db.execute('''INSERT OR IGNORE INTO rp_asset_templates
              (code,category,name,base_price,luxury,min_price_only) VALUES(?,?,?,?,?,?)''',
              (code,item['類別'],item['名稱'],price,int(code in LUXURY_CARS or code in LUXURY_HOMES),int('起' in value or price==0)))
    with RENT_PATH.open(encoding='utf-8-sig',newline='') as fp:
        for item in csv.DictReader(fp):
            db.execute('INSERT OR IGNORE INTO rp_asset_lease_prices VALUES(?,?,?)',
                       (item['官方租屋房型'],int(item['每7天租金(金幣)']),int(item['預設押金(金幣)'])))

def init_assets():
    with transaction() as db: initialize(db)

def character(db, guild, cid, user=None):
    row=db.execute('SELECT * FROM characters WHERE guild_id=? AND id=?',(gid(guild),int(cid))).fetchone()
    if row is None or row['status']!='active': raise AssetError('角色不存在或尚未啟用')
    if user is not None and str(row['owner_id']) != str(user): raise AssetError('你不是這名角色的擁有者')
    return row

def _company(db,guild,bid,user):
    row=db.execute("SELECT * FROM rp_business_entities WHERE guild_id=? AND business_id=? AND status='active'",(gid(guild),str(bid))).fetchone()
    if row is None: raise AssetError('公司不存在或未啟用')
    if str(row['owner_id'])==str(user): return row
    from modules.company_business import has_permission
    if not has_permission(db,gid(guild),str(bid),str(user),'finance'): raise AssetError('未獲得公司財務授權')
    return row

def _account(db,guild,owner_type,owner_id):
    if owner_type=='character': return get_or_create_account(db,gid(guild),'character',str(owner_id),'wallet')
    if owner_type=='organization': return get_or_create_account(db,gid(guild),'organization',str(owner_id),'treasury')
    if owner_type in ('system','escrow'): return get_or_create_account(db,gid(guild),owner_type,str(owner_id),'wallet')
    raise AssetError('不支援的帳戶')

def _pay(db,guild,key,source_type,source_id,target_type,target_id,amount,desc):
    if amount<=0: return
    return _payment(db,gid(guild),key,_account(db,guild,source_type,source_id),_account(db,guild,target_type,target_id),int(amount),desc)

def _event(db,guild,asset,key,kind,actor,**payload):
    db.execute('INSERT INTO rp_asset_events(guild_id,asset_id,event_key,kind,actor_id,payload,created_at) VALUES(?,?,?,?,?,?,?)',
       (gid(guild),asset,key,kind,str(actor),json.dumps(payload,ensure_ascii=False),now()))

def _template(db,guild,code):
    """官方底表 + 伺服器覆寫；其他伺服器不會被本服改價影響。"""
    row=db.execute('''SELECT t.code,
        COALESCE(o.category,t.category) category,COALESCE(o.name,t.name) name,
        COALESCE(o.base_price,t.base_price) base_price,COALESCE(o.luxury,t.luxury) luxury,
        COALESCE(o.active,t.active) active,
        COALESCE(o.min_price_only,t.min_price_only) min_price_only
        FROM rp_asset_templates t LEFT JOIN rp_asset_template_overrides o
        ON o.code=t.code AND o.guild_id=? WHERE t.code=?''',(gid(guild),code)).fetchone()
    return row

def catalog(category=None,guild=None):
    init_assets()
    with get_connection() as db:
        if guild is None:
            rows=db.execute('SELECT * FROM rp_asset_templates WHERE active=1 ORDER BY code').fetchall()
        else:
            rows=db.execute('''SELECT t.code,
                COALESCE(o.category,t.category) category,COALESCE(o.name,t.name) name,
                COALESCE(o.base_price,t.base_price) base_price,COALESCE(o.luxury,t.luxury) luxury,
                COALESCE(o.active,t.active) active,
                COALESCE(o.min_price_only,t.min_price_only) min_price_only
                FROM rp_asset_templates t LEFT JOIN rp_asset_template_overrides o
                ON o.code=t.code AND o.guild_id=?
                WHERE COALESCE(o.active,t.active)=1 ORDER BY t.code''',(gid(guild),)).fetchall()
        return [dict(r) for r in rows if not category or r['category']==category]

def _validate_holder(db,guild,holder_type,holder_id,user=None):
    if holder_type=='character': return character(db,guild,int(holder_id),user)
    if holder_type=='organization': return _company(db,guild,holder_id,user)
    raise AssetError('非法產權類型')

def _owns_all(db,guild,asset,typ,holder):
    row=db.execute('SELECT basis_points FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',
                   (gid(guild),asset,typ,str(holder))).fetchone()
    return bool(row and row['basis_points']==10000)

def _item(db,guild,asset):
    row=db.execute('SELECT a.*, t.category,COALESCE(o.luxury,t.luxury) luxury FROM rp_asset_items a JOIN rp_asset_templates t ON t.code=a.template_code LEFT JOIN rp_asset_template_overrides o ON o.code=t.code AND o.guild_id=a.guild_id WHERE a.guild_id=? AND a.id=? AND a.status=\'active\'',(gid(guild),int(asset))).fetchone()
    if not row: raise AssetError('資產不存在或已失效')
    return row

def _free(db,guild,asset,typ,holder):
    if not _owns_all(db,guild,asset,typ,holder): raise AssetError('僅完整產權持有人可執行此操作；共有資產須先依協議處理')
    if db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0 LIMIT 1",(gid(guild),asset)).fetchone():
        raise AssetError('資產仍有抵押，無法直接交易')
    if db.execute("SELECT 1 FROM rp_asset_orders WHERE guild_id=? AND asset_id=? AND status='open'",(gid(guild),asset)).fetchone():
        raise AssetError('資產已有進行中的掛單')

def _create_asset(db,guild,code,typ,holder,location,origin,price,actor,unique_key=None,parent=None):
    row=_template(db,guild,code)
    if row is not None and not row['active']: row=None
    if not row: raise AssetError('商品不存在')
    value=int(price or row['base_price'])
    if value<=0: raise AssetError('本商品需要官方先設定價格')
    if unique_key and db.execute('SELECT 1 FROM rp_asset_items WHERE guild_id=? AND unique_location_key=?',(gid(guild),unique_key)).fetchone():
        raise AssetError('該房號或地址已有登記')
    if parent is not None: _item(db,guild,parent)
    cur=db.execute('''INSERT INTO rp_asset_items(guild_id,template_code,label,location,unique_location_key,parent_id,base_value,appraised_value,origin,created_at,last_value_at)
       VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(gid(guild),code,row['name'],str(location)[:120],unique_key,parent,value,value,origin,now(),now()))
    asset=cur.lastrowid
    db.execute('INSERT INTO rp_asset_shares VALUES(?,?,?,?,?)',(gid(guild),asset,typ,str(holder),10000))
    _event(db,guild,asset,'create:'+str(asset),'acquire',actor,origin=origin,price=value)
    return asset

def buy(guild,code,character_id,user,location='',unique_key=None):
    """官方全額交易；房屋指定獨立地址時須 unique_key。"""
    with transaction() as db:
        character(db,guild,character_id,user)
        item=_template(db,guild,code)
        if item is not None and not item['active']:item=None
        if not item or item['base_price']<=0 or item['min_price_only']: raise AssetError('此資產需官方指定個別價格，暫不提供直接購買')
        if code.startswith('EST-') and location and location.strip() not in ('未指定','無','無指定') and not unique_key: unique_key='property:'+str(location).strip().lower()
        if code.startswith('EST-') and not unique_key: unique_key='unit:'+uuid.uuid4().hex
        if unique_key and db.execute('SELECT 1 FROM rp_asset_items WHERE guild_id=? AND unique_location_key=?',(gid(guild),unique_key)).fetchone(): raise AssetError('該地址已經出售')
        asset=_create_asset(db,guild,code,'character',character_id,location,'purchase',item['base_price'],user,unique_key=unique_key)
        _pay(db,guild,'asset:buy:'+str(asset),'character',character_id,'system','asset_market',item['base_price'],'購買大型資產')
        return asset

def grant_elder_home(guild,character_id,actor_id,building='元老紀念公寓', *, verified=False):
    """必須由已驗證元老禮包授權的呼叫者啟動；不直接暴露給玩家。"""
    if not verified: raise AssetError('元老住宅必須由已驗證的禮包發放流程進行補登')
    with transaction() as db:
        character(db,guild,character_id)
        key='elder:'+str(character_id)
        found=db.execute('SELECT asset_id FROM rp_asset_events WHERE guild_id=? AND event_key=?',(gid(guild),key)).fetchone()
        if found: return found['asset_id'],False
        asset=_create_asset(db,guild,'EST-01','character',character_id,building+'／元老獨立單間','elder_gift',50000,actor_id,
            unique_key='elder:unit:'+str(character_id))
        _event(db,guild,asset,key,'elder_home',actor_id)
        return asset,True

def holdings(guild,holder_id,typ='character'):
    init_assets()
    with get_connection() as db:
        return [dict(r) for r in db.execute('''SELECT a.*,s.basis_points,t.category,COALESCE(o.luxury,t.luxury) luxury,
            COALESCE((SELECT SUM(due_amount-paid_amount) FROM rp_asset_dues d WHERE d.guild_id=a.guild_id AND d.asset_id=a.id),0) unpaid
            FROM rp_asset_shares s JOIN rp_asset_items a ON a.guild_id=s.guild_id AND a.id=s.asset_id
            JOIN rp_asset_templates t ON t.code=a.template_code
            LEFT JOIN rp_asset_template_overrides o ON o.code=t.code AND o.guild_id=a.guild_id
            WHERE s.guild_id=? AND s.holder_type=? AND s.holder_id=? AND a.status='active' ORDER BY a.id DESC''',
            (gid(guild),typ,str(holder_id)))]

def offer(guild,asset,typ,holder,user,mode,price=0,duration=7,target=None):
    with transaction() as db:
        _validate_holder(db,guild,typ,holder,user)
        a=_item(db,guild,asset);_free(db,guild,asset,typ,holder)
        if mode not in ('sale','gift','auction','rent'): raise AssetError('非法掛單類別')
        if mode=='rent' and a['category']!='不動產': raise AssetError('只有不動產可出租')
        if mode=='rent' and db.execute("SELECT 1 FROM rp_asset_leases WHERE guild_id=? AND asset_id=? AND status='active'",(gid(guild),asset)).fetchone(): raise AssetError('已有生效租約')
        if mode=='auction': duration=int(duration)*24 if int(duration) in TERM_DAYS else int(duration)
        if mode=='auction' and duration not in AUCTION_HOURS: raise AssetError('拍賣時長限 6/24/72 小時')
        if mode in ('sale','auction','rent'): price=_positive(price)
        if mode=='gift' and target is None: raise AssetError('贈與必須指定受贈角色')
        if target is not None: character(db,guild,int(target))
        ends=now()+(duration*3600 if mode=='auction' else duration*86400 if mode in ('sale','gift') else 30*86400)
        cur=db.execute('''INSERT INTO rp_asset_orders(guild_id,asset_id,seller_type,seller_id,price,mode,buyer_type,buyer_id,created_at,ends_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(gid(guild),asset,typ,str(holder),price,mode,'character' if target else None,str(target) if target else None,now(),ends))
        _event(db,guild,asset,'offer:'+str(cur.lastrowid),'listing',user,mode=mode,price=price)
        return cur.lastrowid

def _order(db,guild,order):
    row=db.execute('SELECT * FROM rp_asset_orders WHERE guild_id=? AND id=?',(gid(guild),int(order))).fetchone()
    if not row: raise AssetError('找不到掛單')
    return row

def market(guild,mode=None):
    init_assets()
    with get_connection() as db:
        sql="SELECT o.*,a.label,a.template_code FROM rp_asset_orders o JOIN rp_asset_items a ON a.guild_id=o.guild_id AND a.id=o.asset_id WHERE o.guild_id=? AND o.status='open' AND o.ends_at>?"
        args=[gid(guild),now()]
        if mode:sql+=' AND o.mode=?';args.append(mode)
        return [dict(r) for r in db.execute(sql+' ORDER BY o.id DESC LIMIT 80',args)]

def _move_share(db,guild,asset,from_type,from_id,to_type,to_id):
    db.execute('DELETE FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',
               (gid(guild),asset,from_type,str(from_id)))
    db.execute('INSERT INTO rp_asset_shares VALUES(?,?,?,?,10000)',(gid(guild),asset,to_type,str(to_id)))
    db.execute("UPDATE rp_asset_leases SET landlord_type=?,landlord_id=? WHERE guild_id=? AND asset_id=? AND status='active'",(to_type,str(to_id),gid(guild),asset))

def accept(guild,order,buyer,user):
    with transaction() as db:
        character(db,guild,buyer,user)
        o=_order(db,guild,order)
        if o['status']!='open' or now()>o['ends_at'] or o['mode'] not in ('sale','gift'): raise AssetError('掛單不在可交易狀態')
        if o['seller_type']=='character' and str(buyer)==o['seller_id']: raise AssetError('不能買自己的資產')
        if o['buyer_id'] and str(buyer)!=o['buyer_id']: raise AssetError('這是指定對象的交易')
        _item(db,guild,o['asset_id'])
        if not _owns_all(db,guild,o['asset_id'],o['seller_type'],o['seller_id']):raise AssetError('賣家已不再持有完整產權')
        if o['mode']=='sale':
            fee=math.ceil(o['price']*.02)
            _pay(db,guild,'asset:sale:'+str(order),'character',buyer,o['seller_type'],o['seller_id'],o['price']-fee,'二手資產成交')
            if fee:_pay(db,guild,'asset:fee:'+str(order),'character',buyer,'system','asset_market',fee,'二手資產手續費')
        _move_share(db,guild,o['asset_id'],o['seller_type'],o['seller_id'],'character',buyer)
        db.execute("UPDATE rp_asset_orders SET status='closed',buyer_type='character',buyer_id=?,completed_at=? WHERE guild_id=? AND id=?",(str(buyer),now(),gid(guild),order))
        _event(db,guild,o['asset_id'],'close:'+str(order),'transfer',user,buyer=buyer,price=o['price'])
        return int(o['asset_id'])

def bid(guild,order,bidder,user,amount):
    amount=_positive(amount)
    with transaction() as db:
        character(db,guild,bidder,user)
        o=_order(db,guild,order)
        if o['mode']!='auction' or o['status']!='open' or now()>=o['ends_at']: raise AssetError('拍賣已截止')
        if o['seller_type']=='character' and str(bidder)==o['seller_id']: raise AssetError('禁止自己競標')
        minimum=max(o['price'],o['highest_bid']+max(1,math.ceil(o['highest_bid']*.01)))
        if amount<minimum: raise AssetError(f'最低有效出價為 {minimum:,} 金幣')
        _pay(db,guild,'asset:bid:'+str(order)+':'+str(now())+':'+uuid.uuid4().hex,'character',bidder,'escrow','asset_auction:'+str(order),amount,'拍賣出價託管')
        if o['highest_bidder']:
            _pay(db,guild,'asset:refund:'+str(order)+':'+str(now())+':'+uuid.uuid4().hex,'escrow','asset_auction:'+str(order),'character',o['highest_bidder'],o['highest_bid'],'退回被超越出價')
        additional=300 if o['ends_at']-now()<=300 and o['extension_seconds']<1800 else 0
        db.execute('UPDATE rp_asset_orders SET highest_bid=?,highest_bidder=?,ends_at=ends_at+?,extension_seconds=extension_seconds+? WHERE guild_id=? AND id=?',
                   (amount,str(bidder),additional,additional,gid(guild),order))
        db.execute('INSERT INTO rp_asset_bids VALUES(?,?,?,?,?)',(gid(guild),order,str(bidder),amount,now()))
        return amount

def settle_auction(guild,order):
    with transaction() as db:
        o=_order(db,guild,order)
        if o['mode']!='auction' or o['status']!='open' or o['ends_at']>now(): raise AssetError('拍賣未到結算時間')
        if not o['highest_bidder']:
            db.execute("UPDATE rp_asset_orders SET status='expired',completed_at=? WHERE guild_id=? AND id=?",(now(),gid(guild),order))
            return None
        if not _owns_all(db,guild,o['asset_id'],o['seller_type'],o['seller_id']):raise AssetError('拍賣產權異常，需管理員處置與退還託管款')
        fee=math.ceil(o['highest_bid']*.02)
        _pay(db,guild,'asset:auction:settle:'+str(order),'escrow','asset_auction:'+str(order),o['seller_type'],o['seller_id'],o['highest_bid']-fee,'拍賣成交')
        if fee:_pay(db,guild,'asset:auction:fee:'+str(order),'escrow','asset_auction:'+str(order),'system','asset_market',fee,'拍賣成交費')
        _move_share(db,guild,o['asset_id'],o['seller_type'],o['seller_id'],'character',o['highest_bidder'])
        db.execute("UPDATE rp_asset_orders SET status='closed',buyer_type='character',buyer_id=?,completed_at=? WHERE guild_id=? AND id=?",(o['highest_bidder'],now(),gid(guild),order))
        _event(db,guild,o['asset_id'],'auction:'+str(order),'auction_close','system',buyer=o['highest_bidder'])
        return int(o['asset_id'])

def lease(guild,asset,tenant,user,weeks=1,order_id=None):
    """租金立即支付合法房東，押金託管；獨立住宅不可重租。"""
    days=int(weeks)*7
    if days not in TERM_DAYS:raise AssetError('租期只能 7／14／28 天')
    with transaction() as db:
        character(db,guild,tenant,user)
        if order_id:
            o=_order(db,guild,order_id)
            if o['asset_id']!=int(asset) or o['mode']!='rent' or o['status']!='open' or now()>o['ends_at']:raise AssetError('房源已下架')
            typ,landlord,weekly=o['seller_type'],o['seller_id'],o['price']
        else:
            raise AssetError('請先選擇有效的出租掛單')
        a=_item(db,guild,asset)
        if a['category']!='不動產':raise AssetError('只有房屋可出租')
        if not _owns_all(db,guild,asset,typ,landlord):raise AssetError('房東產權不足')
        if typ=='character' and str(tenant)==str(landlord):raise AssetError('不能租自己的房屋')
        if db.execute("SELECT 1 FROM rp_asset_leases WHERE guild_id=? AND asset_id=? AND status='active'",(gid(guild),asset)).fetchone():raise AssetError('此住宅已被承租')
        deposit=weekly
        cur=db.execute('''INSERT INTO rp_asset_leases(guild_id,asset_id,tenant_id,landlord_type,landlord_id,period_rent,deposit,start_at,ends_at)
           VALUES(?,?,?,?,?,?,?,?,?)''',(gid(guild),asset,tenant,typ,str(landlord),weekly,deposit,now(),now()+days*86400))
        lease_id=cur.lastrowid
        _pay(db,guild,'asset:rent:'+str(lease_id),'character',tenant,typ,landlord,weekly*int(weeks),'房屋預付租金')
        _pay(db,guild,'asset:deposit:'+str(lease_id),'character',tenant,'escrow','asset_lease:'+str(lease_id),deposit,'租屋押金託管')
        db.execute("UPDATE rp_asset_orders SET status='closed',completed_at=? WHERE guild_id=? AND id=?",(now(),gid(guild),order_id))
        _event(db,guild,asset,'lease:'+str(lease_id),'lease_begin',user,tenant=tenant,days=days)
        return lease_id

def close_lease(guild,lease_id,actor=None,force=False):
    with transaction() as db:
        row=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(gid(guild),lease_id)).fetchone()
        if not row or row['status']!='active':raise AssetError('租約不存在或已結束')
        if not force and row['ends_at']>now():raise AssetError('租約尚未到期，提前解約需要契約同意')
        _pay(db,guild,'asset:deposit:return:'+str(lease_id),'escrow','asset_lease:'+str(lease_id),'character',row['tenant_id'],row['deposit'],'返還租屋押金')
        db.execute("UPDATE rp_asset_leases SET status='closed' WHERE guild_id=? AND id=?",(gid(guild),lease_id))
        db.execute('DELETE FROM rp_asset_homes WHERE guild_id=? AND asset_id=? AND character_id=?',(gid(guild),row['asset_id'],row['tenant_id']))
        _event(db,guild,row['asset_id'],'lease:end:'+str(lease_id),'lease_end',actor or 'system')

def generate_maintenance(guild,at=None):
    at=int(at or now()); generated=0
    with transaction() as db:
        rows=db.execute('''SELECT a.id,a.appraised_value,a.template_code,COALESCE(o.luxury,t.luxury) luxury,a.last_value_at,a.created_at
          FROM rp_asset_items a JOIN rp_asset_templates t ON t.code=a.template_code LEFT JOIN rp_asset_template_overrides o ON o.code=t.code AND o.guild_id=a.guild_id WHERE a.guild_id=? AND a.status='active' AND COALESCE(o.luxury,t.luxury)=1 AND a.origin!="official_rental"''',(gid(guild),)).fetchall()
        for a in rows:
            # 第一張帳單在持有滿30日才產生，未滿不提早收費。
            start=a['created_at'] if 'created_at' in a.keys() else a['last_value_at']
            elapsed=(at-start)//(30*86400)
            for period in range(1,min(max(0,elapsed),120)+1):
                due_at=start+period*30*86400
                rate=0.008 if a['template_code'].startswith('CAR-') else 0.004
                fee=max(1,math.ceil(a['appraised_value']*rate))
                cur=db.execute('INSERT OR IGNORE INTO rp_asset_dues(guild_id,asset_id,period_start,due_amount,created_at) VALUES(?,?,?,?,?)',
                  (gid(guild),a['id'],due_at,fee,at))
                generated+=cur.rowcount
    return generated

def pay_maintenance(guild,asset,cid,user):
    with transaction() as db:
        character(db,guild,cid,user);_item(db,guild,asset)
        share=db.execute('SELECT basis_points FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=\'character\' AND holder_id=?',(gid(guild),asset,str(cid))).fetchone()
        if not share:raise AssetError('沒有此資產產權')
        outstanding=db.execute('SELECT COALESCE(SUM(due_amount-paid_amount),0) debt FROM rp_asset_dues WHERE guild_id=? AND asset_id=?',(gid(guild),asset)).fetchone()['debt']
        amount=math.ceil(outstanding*share['basis_points']/10000)
        if amount<=0:raise AssetError('沒有未繳費用')
        # 共有人分攤需要獨立付款追蹤，暫禁止多持份直接結算以免重複繳費。
        if share['basis_points']!=10000:raise AssetError('共有資產維護費請依共有人協議結算；此入口僅支援完整持有')
        periods=[r['period_start'] for r in db.execute('SELECT period_start FROM rp_asset_dues WHERE guild_id=? AND asset_id=? AND due_amount>paid_amount ORDER BY period_start',(gid(guild),asset))]
        key='asset:maintenance:'+str(asset)+':'+':'.join(map(str,periods))
        _pay(db,guild,key,'character',cid,'system','asset_market',amount,'豪宅豪車維護費')
        db.execute('UPDATE rp_asset_dues SET paid_amount=due_amount WHERE guild_id=? AND asset_id=?',(gid(guild),asset))
        _event(db,guild,asset,'maintenance:'+str(uuid.uuid4()),'maintenance_paid',user,amount=amount)
        return amount

def apply_depreciation(guild,at=None):
    at=int(at or now()); changed=0
    with transaction() as db:
        rows=db.execute('''SELECT a.*,t.category FROM rp_asset_items a JOIN rp_asset_templates t ON t.code=a.template_code WHERE a.guild_id=? AND a.status='active' AND t.category='車輛' ''',(gid(guild),)).fetchall()
        for a in rows:
            if a['template_code']=='CAR-10':continue
            periods=min(max(0,(at-a['last_value_at'])//(30*86400)),120)
            if not periods:continue
            value=max(math.ceil(a['base_value']*.2),math.floor(a['appraised_value']*.99**periods))
            db.execute('UPDATE rp_asset_items SET appraised_value=?,last_value_at=last_value_at+? WHERE guild_id=? AND id=?',
                       (value,periods*30*86400,gid(guild),a['id']))
            changed+=1
    return changed

def residence(guild,cid,asset,user):
    with transaction() as db:
        character(db,guild,cid,user);a=_item(db,guild,asset)
        if a['category']!='不動產':raise AssetError('只有住宅可設為住所')
        owned=db.execute("SELECT 1 FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type='character' AND holder_id=?",(gid(guild),asset,str(cid))).fetchone()
        leased=db.execute("SELECT 1 FROM rp_asset_leases WHERE guild_id=? AND asset_id=? AND tenant_id=? AND status='active' AND ends_at>?",(gid(guild),asset,cid,now())).fetchone()
        if not owned and not leased:raise AssetError('你沒有這間住宅的使用權')
        capacity=HOUSING_CAPACITY.get(a['template_code'],2)
        count=db.execute('SELECT COUNT(*) n FROM rp_asset_homes WHERE guild_id=? AND asset_id=?',(gid(guild),asset)).fetchone()['n']
        mine=db.execute('SELECT asset_id FROM rp_asset_homes WHERE guild_id=? AND character_id=?',(gid(guild),cid)).fetchone()
        if count>=capacity and (not mine or mine['asset_id']!=asset):raise AssetError('此住宅正式入住人數已滿')
        db.execute('INSERT INTO rp_asset_homes(guild_id,character_id,asset_id) VALUES(?,?,?) ON CONFLICT(guild_id,character_id) DO UPDATE SET asset_id=excluded.asset_id',(gid(guild),cid,asset))

def financial_summary(guild,cid):
    assets=holdings(guild,cid)
    return {'assets':len(assets),'gross':sum(a['appraised_value']*a['basis_points']//10000 for a in assets),
           'outstanding_maintenance':sum(int(a['unpaid']) for a in assets if a['basis_points']==10000)}

def rent_official(guild,code,tenant,user,weeks=1):
    """官方生成獨立房號的出租單位；歸官方所有，租客只獲使用權。"""
    if int(weeks) not in (1,2,4): raise AssetError('租期只能是 7/14/28 天')
    mapping={'EST-01':'公寓單間','EST-02':'獨立套房','EST-03':'一房公寓',
      'EST-04':'兩房公寓','EST-05':'三房住宅','EST-06':'高級住宅',
      'EST-07':'頂樓豪華公寓','EST-08':'獨棟別墅','EST-09':'豪華莊園'}
    if code not in mapping:raise AssetError('此類房型沒有官方租賃方案')
    with transaction() as db:
        character(db,guild,tenant,user)
        rate=db.execute('SELECT * FROM rp_asset_lease_prices WHERE name=?',(mapping[code],)).fetchone()
        if not rate: raise AssetError('官方租金尚未設定')
        item=_create_asset(db,guild,code,'organization','official_housing', '官方出租住宅','official_rental',
          _template(db,guild,code)['base_price'],
          'system',unique_key='rent:official:'+uuid.uuid4().hex)
        start=now()
        cur=db.execute('''INSERT INTO rp_asset_leases(guild_id,asset_id,tenant_id,landlord_type,landlord_id,period_rent,deposit,start_at,ends_at)
          VALUES(?,?,?,?,?,?,?,?,?)''',(gid(guild),item,tenant,'organization','official_housing',int(rate['weekly_rent']),int(rate['deposit']),start,start+int(weeks)*7*86400))
        lease_id=cur.lastrowid
        _pay(db,guild,'asset:official:rent:'+str(lease_id),'character',tenant,'system','asset_market',int(rate['weekly_rent'])*int(weeks),'官方出租住宅租金')
        _pay(db,guild,'asset:official:deposit:'+str(lease_id),'character',tenant,'escrow','asset_lease:'+str(lease_id),int(rate['deposit']),'官方租房押金')
        _event(db,guild,item,'lease:'+str(lease_id),'lease_begin',user,tenant=tenant,official=True)
        return item,lease_id


def record_existing_bank_lien(guild,asset,bank_reference,amount,verified=False):
    """僅供銀行已核准／入帳的可信內部流程呼叫；玩家 UI 不提供此操作。"""
    if not verified:raise AssetError('抵押設定只能由已核實銀行貸款流程執行')
    amount=_positive(amount)
    with transaction() as db:
        _item(db,guild,asset)
        existing=db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0",(gid(guild),asset)).fetchone()
        if existing:raise AssetError('已有未清償抵押')
        db.execute('INSERT INTO rp_asset_lien VALUES(?,?,?,?,?)',(gid(guild),asset,str(bank_reference),amount,'active'))
        _event(db,guild,asset,'lien:'+str(bank_reference),'lien_add','bank',amount=amount)


def clear_bank_lien(guild,asset,reference,verified=False):
    if not verified:raise AssetError('抵押解除只能由銀行已結清流程執行')
    with transaction() as db:
        db.execute("UPDATE rp_asset_lien SET outstanding_amount=0,status='cleared' WHERE guild_id=? AND asset_id=? AND bank_reference=?",(gid(guild),asset,str(reference)))


def transfer_share(guild,asset,from_type,from_id,to_type,to_id,basis_points,user,*, recipient_accepted=False):
    """僅持分移轉；完整資產販售仍走 offer/accept 及金幣總帳。"""
    if not recipient_accepted: raise AssetError('共有產權移轉前需取得受贈方同意')
    bp=int(basis_points)
    if bp<1 or bp>10000:raise AssetError('持分必須介於 0.01%～100%')
    with transaction() as db:
        _validate_holder(db,guild,from_type,from_id,user)
        _validate_holder(db,guild,to_type,to_id)
        _item(db,guild,asset)
        if db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0",(gid(guild),asset)).fetchone():raise AssetError('抵押中不得移轉持分')
        if db.execute("SELECT 1 FROM rp_asset_orders WHERE guild_id=? AND asset_id=? AND status='open'",(gid(guild),asset)).fetchone():raise AssetError('掛單中不得移轉持分')
        from core.asset_completions import _block_unsettled_share_transfer
        _block_unsettled_share_transfer(db,guild,asset)
        row=db.execute('SELECT basis_points FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',(gid(guild),asset,from_type,str(from_id))).fetchone()
        if not row or row['basis_points']<bp:raise AssetError('可移轉的產權份額不足')
        if from_type==to_type and str(from_id)==str(to_id):raise AssetError('不可移轉給自己')
        remain=row['basis_points']-bp
        if remain:db.execute('UPDATE rp_asset_shares SET basis_points=? WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',(remain,gid(guild),asset,from_type,str(from_id)))
        else:db.execute('DELETE FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',(gid(guild),asset,from_type,str(from_id)))
        db.execute('''INSERT INTO rp_asset_shares(guild_id,asset_id,holder_type,holder_id,basis_points) VALUES(?,?,?,?,?)
          ON CONFLICT(guild_id,asset_id,holder_type,holder_id) DO UPDATE SET basis_points=basis_points+excluded.basis_points''',(gid(guild),asset,to_type,str(to_id),bp))
        _event(db,guild,asset,'share:'+uuid.uuid4().hex,'share_transfer',user,amount=bp,to=str(to_id))
        return bp


def buy_for_company(guild,code,business_id,user,location='',unique_key=None):
    """使用正式公司金庫；只允許負責人或取得財務授權者。"""
    with transaction() as db:
        _company(db,guild,business_id,user)
        item=_template(db,guild,code)
        if item is not None and not item['active']:item=None
        if not item or item['base_price']<=0 or item['min_price_only']:raise AssetError('需個別定價，不可自動成交')
        if code.startswith('EST-') and location and location.strip() not in ('未指定','無','無指定') and not unique_key:unique_key='property:'+str(location).strip().lower()
        if code.startswith('EST-') and not unique_key:unique_key='unit:'+uuid.uuid4().hex
        if unique_key and db.execute('SELECT 1 FROM rp_asset_items WHERE guild_id=? AND unique_location_key=?',(gid(guild),unique_key)).fetchone():raise AssetError('指定房屋已被購買')
        asset=_create_asset(db,guild,code,'organization',business_id,location,'company_purchase',item['base_price'],user,unique_key=unique_key)
        _pay(db,guild,'asset:company:buy:'+str(asset),'organization',business_id,'system','asset_market',item['base_price'],'公司購置大型資產')
        return asset


def close_expired_leases(guild,at=None):
    current=int(at or now());closed=0
    with get_connection() as db:
        leases=[row['id'] for row in db.execute("SELECT id FROM rp_asset_leases WHERE guild_id=? AND status='active' AND ends_at<=?",(gid(guild),current))]
    for lease_id in leases:
        try:
            close_lease(guild,lease_id,force=False)
            closed+=1
        except AssetError:
            # 允許重複排程競爭；個別結算出錯不能影響其他租約。
            continue
    return closed


def get_unpaid_maintenance(guild,asset):
    with get_connection() as db:
        row=db.execute('SELECT COALESCE(SUM(due_amount-paid_amount),0) amount,MIN(period_start) oldest FROM rp_asset_dues WHERE guild_id=? AND asset_id=? AND due_amount>paid_amount',(gid(guild),int(asset))).fetchone()
        amount=int(row['amount'] or 0)
        elapsed=(now()-row['oldest'])//86400 if row['oldest'] else 0
        return {'due':amount,'days_overdue':elapsed,'status':'normal' if not amount else 'pending' if elapsed<=7 else 'overdue' if elapsed<=14 else 'restricted'}

def expire_unfilled_offers(guild,at=None):
    """到期無競標/出售/贈與/出租的掛單解除鎖定；已有效出價拍賣另走 settle_auction。"""
    current=int(at or now())
    with transaction() as db:
        cur=db.execute("""UPDATE rp_asset_orders SET status='expired',completed_at=?
            WHERE guild_id=? AND status='open' AND ends_at<=?
              AND (mode!='auction' OR highest_bidder IS NULL)""",(current,gid(guild),current))
        return cur.rowcount
