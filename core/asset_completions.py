"""大型資產補充：共有產權、租約續租/雙方解約、共有維護費、官方商品管理。

與 core.large_assets 使用同一個 SQLite 交易和金幣雙分錄。
不在此虛構銀行貸款批准，也不自動沒收角色/公司財產。
"""
from __future__ import annotations
import math
import time
import uuid
from core import large_assets as a
from core.database import transaction, get_connection


def initialize(db):
    a.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_asset_share_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id TEXT NOT NULL,asset_id INTEGER NOT NULL,
        from_type TEXT NOT NULL,from_id TEXT NOT NULL,to_character INTEGER NOT NULL,
        basis_points INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,UNIQUE(guild_id,id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_asset_share_dues (
        guild_id TEXT NOT NULL,asset_id INTEGER NOT NULL,period_start INTEGER NOT NULL,
        holder_type TEXT NOT NULL,holder_id TEXT NOT NULL,amount INTEGER NOT NULL,
        paid INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild_id,asset_id,period_start,holder_type,holder_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_asset_lease_terminations (
        guild_id TEXT NOT NULL,lease_id INTEGER NOT NULL,requester TEXT NOT NULL,
        requested_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
        PRIMARY KEY(guild_id,lease_id))''')


def _ensure(guild, cid, user):
    with transaction() as db:
        initialize(db)
        return a.character(db,guild,cid,user)


def request_share(guild,asset,from_character,to_character,basis_points,user):
    bp=int(basis_points)
    if not 1<=bp<=10000:raise a.AssetError('產權份額必須在 0.01% 至 100%')
    if int(from_character)==int(to_character):raise a.AssetError('不能將份額轉給自己')
    with transaction() as db:
        initialize(db)
        a.character(db,guild,from_character,user)
        a.character(db,guild,to_character)
        a._item(db,guild,asset)
        source=db.execute("SELECT basis_points FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type='character' AND holder_id=?",(a.gid(guild),int(asset),str(from_character))).fetchone()
        if not source or source['basis_points']<bp:raise a.AssetError('可轉移的份額不足')
        if db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0",(a.gid(guild),int(asset))).fetchone():raise a.AssetError('抵押中不得移轉持分')
        if db.execute("SELECT 1 FROM rp_asset_orders WHERE guild_id=? AND asset_id=? AND status='open'",(a.gid(guild),int(asset))).fetchone():raise a.AssetError('有進行中的掛單')
        cur=db.execute('INSERT INTO rp_asset_share_requests(guild_id,asset_id,from_type,from_id,to_character,basis_points,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
            (a.gid(guild),int(asset),'character',str(from_character),int(to_character),bp,a.now(),a.now()+7*86400))
        return cur.lastrowid


def accept_share(guild,request_id,to_character,user):
    with transaction() as db:
        initialize(db)
        a.character(db,guild,to_character,user)
        req=db.execute('SELECT * FROM rp_asset_share_requests WHERE guild_id=? AND id=?',(a.gid(guild),int(request_id))).fetchone()
        if not req or req['status']!='pending' or req['expires_at']<a.now():raise a.AssetError('產權邀請已失效')
        if req['to_character']!=int(to_character):raise a.AssetError('這不是你的產權邀請')
        asset=req['asset_id'];source=req['from_id'];bp=req['basis_points']
        a._item(db,guild,asset)
        if db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0",(a.gid(guild),asset)).fetchone():raise a.AssetError('抵押中的資產不可移轉')
        if db.execute("SELECT 1 FROM rp_asset_orders WHERE guild_id=? AND asset_id=? AND status='open'",(a.gid(guild),asset)).fetchone():raise a.AssetError('掛單中的資產不可移轉')
        row=db.execute("SELECT basis_points FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type='character' AND holder_id=?",(a.gid(guild),asset,source)).fetchone()
        if not row or row['basis_points']<bp:raise a.AssetError('原持有人剩餘份額不足')
        remaining=row['basis_points']-bp
        if remaining:
            db.execute("UPDATE rp_asset_shares SET basis_points=? WHERE guild_id=? AND asset_id=? AND holder_type='character' AND holder_id=?",(remaining,a.gid(guild),asset,source))
        else:
            db.execute("DELETE FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type='character' AND holder_id=?",(a.gid(guild),asset,source))
        db.execute("INSERT INTO rp_asset_shares(guild_id,asset_id,holder_type,holder_id,basis_points) VALUES(?,?,'character',?,?) ON CONFLICT(guild_id,asset_id,holder_type,holder_id) DO UPDATE SET basis_points=basis_points+excluded.basis_points",(a.gid(guild),asset,str(to_character),bp))
        db.execute("UPDATE rp_asset_share_requests SET status='accepted' WHERE guild_id=? AND id=?",(a.gid(guild),int(request_id)))
        a._event(db,guild,asset,'share:accept:'+str(request_id),'share_transfer',user,from_character=source,to_character=to_character,basis_points=bp)
        return asset


def renew_lease(guild,lease_id,tenant,user,weeks):
    weeks=int(weeks)
    if weeks not in (1,2,4):raise a.AssetError('續租僅限 7、14、28 天')
    with transaction() as db:
        initialize(db)
        a.character(db,guild,tenant,user)
        lease=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(a.gid(guild),int(lease_id))).fetchone()
        if not lease or lease['status']!='active' or lease['tenant_id']!=int(tenant):raise a.AssetError('無權續租這份租約')
        if lease['ends_at']<=a.now():raise a.AssetError('租約已到期，請重新申請')
        # 玩家房東已將租約成立視為同意在原價格下履行本期，不等於同意無限期自動續租。
        # 因此手動續租只提供官方出租房；玩家出租房需另行簽約。
        if lease['landlord_type']!='organization' or lease['landlord_id']!='official_housing':raise a.AssetError('玩家房源續租需要房東同意並重新簽約')
        amount=int(lease['period_rent'])*weeks
        a._pay(db,guild,'asset:lease:renew:'+str(lease_id)+':'+str(lease['ends_at']),'character',tenant,'system','asset_market',amount,'官方房屋租約續約')
        new_end=lease['ends_at']+weeks*7*86400
        db.execute('UPDATE rp_asset_leases SET ends_at=? WHERE guild_id=? AND id=?',(new_end,a.gid(guild),int(lease_id)))
        a._event(db,guild,lease['asset_id'],'lease:renew:'+str(lease_id)+':'+str(lease['ends_at']),'lease_renew',user,days=weeks*7)
        return new_end


def request_early_termination(guild,lease_id,tenant,user):
    with transaction() as db:
        initialize(db)
        a.character(db,guild,tenant,user)
        lease=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(a.gid(guild),int(lease_id))).fetchone()
        if not lease or lease['status']!='active' or lease['tenant_id']!=int(tenant):raise a.AssetError('無效的租約')
        db.execute('INSERT INTO rp_asset_lease_terminations(guild_id,lease_id,requester,requested_at,status) VALUES(?,?,?,?,\'pending\') ON CONFLICT(guild_id,lease_id) DO UPDATE SET requester=excluded.requester,requested_at=excluded.requested_at,status=\'pending\'',
            (a.gid(guild),int(lease_id),str(tenant),a.now()))
        return lease['landlord_type'],lease['landlord_id']


def approve_early_termination(guild,lease_id,landlord_character,user):
    with transaction() as db:
        initialize(db)
        a.character(db,guild,landlord_character,user)
        lease=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(a.gid(guild),int(lease_id))).fetchone()
        req=db.execute('SELECT * FROM rp_asset_lease_terminations WHERE guild_id=? AND lease_id=? AND status=\'pending\'',(a.gid(guild),int(lease_id))).fetchone()
        if not lease or lease['status']!='active' or not req:raise a.AssetError('沒有可批准的提前退租申請')
        if lease['landlord_type']!='character' or lease['landlord_id']!=str(landlord_character):raise a.AssetError('必須由有效房東批准')
        # 原則：已開始的 7 日週期不退；只退仍完整未開始的週期。
        remaining=max(0,lease['ends_at']-a.now())
        unused=max(0,remaining//(7*86400))
        refund=min(unused*int(lease['period_rent']),int(lease['period_rent'])*4)
        if refund:
            a._pay(db,guild,'asset:lease:early:rent:'+str(lease_id),lease['landlord_type'],lease['landlord_id'],'character',lease['tenant_id'],refund,'提前退租未使用完整週期退款')
        a._pay(db,guild,'asset:lease:early:deposit:'+str(lease_id),'escrow','asset_lease:'+str(lease_id),'character',lease['tenant_id'],int(lease['deposit']),'提前退租押金返還')
        db.execute("UPDATE rp_asset_leases SET status='closed',ends_at=? WHERE guild_id=? AND id=?",(a.now(),a.gid(guild),int(lease_id)))
        db.execute("UPDATE rp_asset_lease_terminations SET status='approved' WHERE guild_id=? AND lease_id=?",(a.gid(guild),int(lease_id)))
        db.execute('DELETE FROM rp_asset_homes WHERE guild_id=? AND asset_id=? AND character_id=?',(a.gid(guild),lease['asset_id'],lease['tenant_id']))
        a._event(db,guild,lease['asset_id'],'lease:early:'+str(lease_id),'lease_end',user,refund=refund)
        return refund


def prepare_shared_dues(db,guild,asset):
    dues=db.execute('SELECT * FROM rp_asset_dues WHERE guild_id=? AND asset_id=?',(a.gid(guild),int(asset))).fetchall()
    for d in dues:
        exists=db.execute('SELECT 1 FROM rp_asset_share_dues WHERE guild_id=? AND asset_id=? AND period_start=? LIMIT 1',(a.gid(guild),int(asset),d['period_start'])).fetchone()
        if exists:continue
        shares=db.execute('SELECT * FROM rp_asset_shares WHERE guild_id=? AND asset_id=? ORDER BY holder_type,holder_id',(a.gid(guild),int(asset))).fetchall()
        if not shares:continue
        remaining=int(d['due_amount'])
        for idx,s in enumerate(shares):
            portion=remaining if idx==len(shares)-1 else int(d['due_amount'])*s['basis_points']//10000
            remaining-=portion
            db.execute('INSERT INTO rp_asset_share_dues VALUES(?,?,?,?,?,?,0)',(a.gid(guild),int(asset),d['period_start'],s['holder_type'],s['holder_id'],portion))


def pay_shared_maintenance(guild,asset,holder_type,holder_id,user):
    with transaction() as db:
        initialize(db)
        a._validate_holder(db,guild,holder_type,holder_id,user)
        a._item(db,guild,asset)
        owned=db.execute('SELECT 1 FROM rp_asset_shares WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=?',(a.gid(guild),int(asset),holder_type,str(holder_id))).fetchone()
        if not owned:raise a.AssetError('不是此資產共有人')
        prepare_shared_dues(db,guild,asset)
        parts=db.execute('SELECT * FROM rp_asset_share_dues WHERE guild_id=? AND asset_id=? AND holder_type=? AND holder_id=? AND paid=0 ORDER BY period_start',(a.gid(guild),int(asset),holder_type,str(holder_id))).fetchall()
        if not parts:raise a.AssetError('沒有未繳的共有資產維護費')
        amount=sum(p['amount'] for p in parts)
        if amount<=0:raise a.AssetError('不需要付款')
        a._pay(db,guild,'asset:shared:due:'+str(asset)+':'+holder_type+':'+str(holder_id)+':'+':'.join(str(p['period_start']) for p in parts),holder_type,holder_id,'system','asset_market',amount,'共有資產維護費')
        db.executemany('UPDATE rp_asset_share_dues SET paid=1 WHERE guild_id=? AND asset_id=? AND period_start=? AND holder_type=? AND holder_id=?',[(a.gid(guild),int(asset),p['period_start'],holder_type,str(holder_id)) for p in parts])
        for period in {p['period_start'] for p in parts}:
            total=db.execute('SELECT COALESCE(SUM(amount),0) FROM rp_asset_share_dues WHERE guild_id=? AND asset_id=? AND period_start=? AND paid=1',(a.gid(guild),int(asset),period)).fetchone()[0]
            db.execute('UPDATE rp_asset_dues SET paid_amount=MIN(due_amount,?) WHERE guild_id=? AND asset_id=? AND period_start=?',(total,a.gid(guild),int(asset),period))
        a._event(db,guild,asset,'shared:maintenance:'+uuid.uuid4().hex,'maintenance_paid',user,amount=amount)
        return amount


def update_template(guild,code,user,*,name=None,base_price=None,active=None,luxury=None):
    """只更新本伺服器的商品覆寫設定，群主身分仍由 UI 再核對。"""
    with transaction() as db:
        initialize(db)
        code=str(code).strip().upper()
        row=a._template(db,guild,code)
        if not row:raise a.AssetError('找不到資產模板')
        new_price=row['base_price'] if base_price is None else int(base_price)
        if new_price<0 or new_price>10**12:raise a.AssetError('商品價格無效')
        new_name=row['name'] if name is None else str(name).strip()[:80]
        if not new_name:raise a.AssetError('商品名稱不能為空')
        db.execute('''INSERT INTO rp_asset_template_overrides
          (guild_id,code,category,name,base_price,luxury,active,min_price_only)
          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,code) DO UPDATE SET
          name=excluded.name,base_price=excluded.base_price,luxury=excluded.luxury,
          active=excluded.active,min_price_only=excluded.min_price_only''',(
          a.gid(guild),code,row['category'],new_name,new_price,
          row['luxury'] if luxury is None else int(bool(luxury)),
          row['active'] if active is None else int(bool(active)),int(new_price==0)))
        return code


def mortgage_asset_id(purpose):
    """貸款用途首段填「資產抵押#編號」，不符合格式的舊貸款不會自動設定抵押。"""
    import re
    result=re.match(r'^資產抵押#([1-9][0-9]*)(?:\s|$)',str(purpose))
    return int(result.group(1)) if result else None


def _mortgage_limit(item):
    code=item['template_code']
    if code.startswith('EST-'):return 60 if item['luxury'] else 70
    if code.startswith('CAR-'):return 40 if item['luxury'] else 50
    if code.startswith(('SHIP-','AIR-')):return 40
    if code.startswith('COL-'):return 30
    return 0


def validate_mortgage(db,guild,character_id,asset_id,amount):
    """銀行同筆交易內核對，不能靠玩家文字自行建立抵押。"""
    initialize(db)
    item=a._item(db,guild,asset_id)
    if not a._owns_all(db,guild,asset_id,'character',character_id):raise a.AssetError('銀行抵押需要借款角色完整持有此資產')
    if db.execute("SELECT 1 FROM rp_asset_lien WHERE guild_id=? AND asset_id=? AND status='active' AND outstanding_amount>0",(a.gid(guild),int(asset_id))).fetchone():raise a.AssetError('資產已有有效抵押')
    if db.execute("SELECT 1 FROM rp_asset_orders WHERE guild_id=? AND asset_id=? AND status='open'",(a.gid(guild),int(asset_id))).fetchone():raise a.AssetError('已掛牌資產不可抵押')
    cap=item['appraised_value']*_mortgage_limit(item)//100
    if int(amount)>cap or cap<=0:raise a.AssetError(f'核貸金額超過此資產可抵押上限 {cap:,} 金幣')
    return item


def bind_bank_loan(db,guild,loan_id,character_id,asset_id,amount):
    validate_mortgage(db,guild,character_id,asset_id,amount)
    db.execute('INSERT INTO rp_asset_lien(guild_id,asset_id,bank_reference,outstanding_amount,status) VALUES(?,?,?,?,\'active\')',
        (a.gid(guild),int(asset_id),'loan:'+str(loan_id),int(amount)))
    a._event(db,guild,asset_id,'lien:bank:'+str(loan_id),'lien_add','bank',loan_id=int(loan_id),amount=int(amount))


def release_bank_loan(db,guild,loan_id):
    db.execute("UPDATE rp_asset_lien SET outstanding_amount=0,status='cleared' WHERE guild_id=? AND bank_reference=? AND status='active'",(a.gid(guild),'loan:'+str(loan_id)))


def validate_liquidation(guild,holder_type,holder_id):
    """在角色刪除／公司解散之前先調用：避免無聲遺失有價資產。"""
    with transaction() as db:
        initialize(db)
        held=db.execute('SELECT asset_id,basis_points FROM rp_asset_shares WHERE guild_id=? AND holder_type=? AND holder_id=?',(a.gid(guild),holder_type,str(holder_id))).fetchall()
        if held:
            raise a.AssetError(f'尚有 {len(held)} 項大型資產持分；請先完成法定清算，不可直接刪除持有人')
        tenants=db.execute("SELECT COUNT(*) n FROM rp_asset_leases WHERE guild_id=? AND status='active' AND tenant_id=?",(a.gid(guild),str(holder_id))).fetchone()['n'] if holder_type=='character' else 0
        if tenants:raise a.AssetError('角色仍有未結束租約；須先完成退租與押金結算')
    return True


def create_template(guild,code,category,name,base_price,luxury=False):
    """各服獨立上架。同代碼可在不同服以不同售價與名稱存在。"""
    allowed={'CAR':'車輛','EST':'不動產','SHIP':'船舶','AIR':'航空器','COL':'珍稀收藏','SPC':'特殊資產'}
    code=str(code).strip().upper()
    if '-' not in code or code.split('-',1)[0] not in allowed:raise a.AssetError('商品代碼須以 CAR/EST/SHIP/AIR/COL/SPC 開頭')
    expected=allowed[code.split('-',1)[0]]
    if str(category).strip()!=expected:raise a.AssetError('商品分類與代碼前綴不一致')
    if not 1<=len(str(name).strip())<=80:raise a.AssetError('商品名稱長度不正確')
    value=int(base_price)
    if value<0 or value>10**12:raise a.AssetError('商品售價不合法')
    with transaction() as db:
        initialize(db)
        if db.execute('SELECT 1 FROM rp_asset_template_overrides WHERE guild_id=? AND code=?',(a.gid(guild),code)).fetchone():raise a.AssetError('此伺服器商品代碼已存在')
        global_template=db.execute('SELECT * FROM rp_asset_templates WHERE code=?',(code,)).fetchone()
        if global_template and global_template['active']:
            raise a.AssetError('官方預設商品已存在；請使用「官方修改資產」')
        if global_template and global_template['category']!=expected:
            raise a.AssetError('此代碼已由其他類別使用')
        if not global_template:
            db.execute('INSERT INTO rp_asset_templates(code,category,name,base_price,luxury,active,min_price_only) VALUES(?,?,?,0,0,0,1)',
                (code,expected,'自訂資產模板'))
        db.execute('INSERT INTO rp_asset_template_overrides(guild_id,code,category,name,base_price,luxury,active,min_price_only) VALUES(?,?,?,?,?,?,1,?)',
            (a.gid(guild),code,expected,str(name).strip(),value,int(bool(luxury)),int(value==0)))
    return code


def repair_elder_home_for_unlocked(guild,character_id):
    """歷史已正式解鎖開城元老的角色才可以補房，且完全冪等。"""
    from modules.achievement_rewards import prepare_rewards, grant_one_item
    with transaction() as db:
        initialize(db)
        a.character(db,guild,character_id)
        row=db.execute('''SELECT d.id FROM rp_character_achievements ca
           JOIN rp_achievement_definitions d ON ca.guild_id=d.guild_id AND ca.achievement_id=d.id
           WHERE ca.guild_id=? AND ca.character_id=? AND d.code='founder' ''',(a.gid(guild),int(character_id))).fetchone()
        if not row:raise a.AssetError('此角色沒有已解鎖的開城元老稱號，不能補發')
        title=int(row['id'])
    prepare_rewards(guild,character_id,title)
    result=grant_one_item(guild,character_id,title,'ordinary_apartment_room')
    # 兼容過去將獎勵標記 done 卻未實際建房的歷史資料：仍須上述正式稱號核對。
    asset,created=a.grant_elder_home(guild,character_id,'achievement:founder:repair',verified=True)
    return asset,created,result


def dispatch_maintenance_notices(guild):
    """定期補送欠費通知；使用 dedupe_key 確保每張帳單最多一則。"""
    from modules.notifications import queue_notification
    with transaction() as db:
        initialize(db)
        rows=db.execute('''SELECT d.asset_id,d.period_start,d.due_amount,a.label,s.holder_type,s.holder_id
          FROM rp_asset_dues d JOIN rp_asset_items a ON a.id=d.asset_id AND a.guild_id=d.guild_id
          JOIN rp_asset_shares s ON s.asset_id=d.asset_id AND s.guild_id=d.guild_id
          WHERE d.guild_id=? AND d.due_amount>d.paid_amount''',(a.gid(guild),)).fetchall()
        notifications=[]
        for r in rows:
            if r['holder_type']=='character':
                notifications.append((int(r['holder_id']),r['asset_id'],r['period_start'],r['label'],r['due_amount']))
    count=0
    for cid,asset,period,label,amount in notifications:
        try:
            if queue_notification(int(guild),'private','bank',f'🏛️ 大型資產維護費：#{asset} {label}，本期帳單 {amount:,} 金幣。請至 /資產 繳費；未付款可能限制附加功能。',character_id=cid,
                     dedupe_key=f'asset:due:{guild}:{asset}:{period}:{cid}'):
                count+=1
        except Exception:
            # 不因私人論壇失效就刪掉帳單；下輪可重試
            continue
    return count


def request_player_renewal(guild,lease_id,tenant,user,weeks):
    weeks=int(weeks)
    if weeks not in (1,2,4):raise a.AssetError('續租僅限 1／2／4 週')
    with transaction() as db:
        initialize(db)
        a.character(db,guild,tenant,user)
        db.execute('''CREATE TABLE IF NOT EXISTS rp_asset_lease_renewals (
           guild_id TEXT NOT NULL,lease_id INTEGER NOT NULL,tenant_id INTEGER NOT NULL,
           weeks INTEGER NOT NULL,requested_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
           PRIMARY KEY(guild_id,lease_id))''')
        lease=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(a.gid(guild),int(lease_id))).fetchone()
        if not lease or lease['status']!='active' or lease['tenant_id']!=int(tenant) or lease['ends_at']<=a.now():raise a.AssetError('租約不存在、已到期或不屬於你')
        if lease['landlord_type']!='character':raise a.AssetError('官方房源直接使用官方續租')
        db.execute('''INSERT INTO rp_asset_lease_renewals VALUES(?,?,?,?,?,'pending')
            ON CONFLICT(guild_id,lease_id) DO UPDATE SET tenant_id=excluded.tenant_id,
            weeks=excluded.weeks,requested_at=excluded.requested_at,status='pending' ''',
            (a.gid(guild),int(lease_id),int(tenant),weeks,a.now()))
        return int(lease['landlord_id'])


def approve_player_renewal(guild,lease_id,landlord,user):
    with transaction() as db:
        initialize(db)
        a.character(db,guild,landlord,user)
        db.execute('''CREATE TABLE IF NOT EXISTS rp_asset_lease_renewals (
           guild_id TEXT NOT NULL,lease_id INTEGER NOT NULL,tenant_id INTEGER NOT NULL,
           weeks INTEGER NOT NULL,requested_at INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
           PRIMARY KEY(guild_id,lease_id))''')
        lease=db.execute('SELECT * FROM rp_asset_leases WHERE guild_id=? AND id=?',(a.gid(guild),int(lease_id))).fetchone()
        request=db.execute("SELECT * FROM rp_asset_lease_renewals WHERE guild_id=? AND lease_id=? AND status='pending'",(a.gid(guild),int(lease_id))).fetchone()
        if not lease or lease['status']!='active' or lease['ends_at']<=a.now() or not request:raise a.AssetError('沒有有效續租申請')
        if lease['landlord_type']!='character' or lease['landlord_id']!=str(landlord):raise a.AssetError('只有目前房東可以批准')
        amount=int(lease['period_rent'])*int(request['weeks'])
        a._pay(db,guild,'asset:lease:renew:'+str(lease_id)+':'+str(lease['ends_at']),'character',lease['tenant_id'],'character',landlord,amount,'雙方同意續租')
        new_end=lease['ends_at']+int(request['weeks'])*7*86400
        db.execute('UPDATE rp_asset_leases SET ends_at=? WHERE guild_id=? AND id=?',(new_end,a.gid(guild),int(lease_id)))
        db.execute("UPDATE rp_asset_lease_renewals SET status='accepted' WHERE guild_id=? AND lease_id=?",(a.gid(guild),int(lease_id)))
        a._event(db,guild,lease['asset_id'],'lease:renew:'+str(lease_id)+':'+str(lease['ends_at']),'lease_renew',user,days=int(request['weeks'])*7)
        return new_end


def appraise_asset(guild,asset_id,new_value,admin_user,*, extraordinary=False):
    """由群主依正式市場事件調整估值；不讓玩家買賣價直接污染全服估值。

    一般房產：每 30 日最多 ±5%；收藏品：每 30 日最多 ±10%。
    車輛仍由既有折舊任務處理。重大特殊估值需另有人工流程，不開放 UI 例外。
    """
    value=int(new_value)
    if value<=0 or value>10**12:raise a.AssetError('估值必須為正整數')
    with transaction() as db:
        initialize(db)
        item=a._item(db,guild,asset_id)
        if item['category']=='不動產':limit=0.05
        elif item['category']=='珍稀收藏':limit=0.10
        else:raise a.AssetError('此介面只允許房產及收藏品市場估值')
        previous=int(item['appraised_value'])
        if not extraordinary:
            if a.now()-int(item['last_value_at'])<30*86400:raise a.AssetError('一般市場估值每 30 天最多更新一次')
            lower=math.ceil(previous*(1-limit));upper=math.floor(previous*(1+limit))
            if not lower<=value<=upper:raise a.AssetError(f'本期估值必須介於 {lower:,}～{upper:,} 金幣')
        db.execute('UPDATE rp_asset_items SET appraised_value=?,last_value_at=? WHERE guild_id=? AND id=?',(value,a.now(),a.gid(guild),int(asset_id)))
        a._event(db,guild,asset_id,'appraisal:'+uuid.uuid4().hex,'appraisal',admin_user,old=previous,new=value)
        return previous,value
