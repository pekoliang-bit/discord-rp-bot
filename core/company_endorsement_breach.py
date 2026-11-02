"""雙方協議的代言違約和解紀錄；不自動認定違約方或扣款。"""
from core.database import transaction, get_connection
from core.company_endorsement_lifecycle import _setup, _parties, _audit

def _init(db):
    _setup(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_breach_settlements (
    guild_id TEXT NOT NULL, endorsement_id INTEGER NOT NULL, payer_id TEXT NOT NULL,
    payee_id TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
    reason TEXT NOT NULL, proposed_by TEXT NOT NULL, accepted_by TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','accepted','cancelled','paid')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(guild_id,endorsement_id))""")
    columns={r['name'] for r in db.execute("PRAGMA table_info(rp_endorsement_breach_settlements)")}
    for name,typ in (("paid_at","TEXT"),("payer_character_id","INTEGER"),("payee_character_id","INTEGER")):
        if name not in columns: db.execute(f"ALTER TABLE rp_endorsement_breach_settlements ADD COLUMN {name} {typ}")
    # 舊表 CHECK 不包含 paid 時 SQLite 無法直接修改；以 paid 付款欄位作為相容狀態。

def propose(guild_id,endorsement_id,actor_id,payer_id,amount,reason):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id);payer=str(payer_id)
    amount=int(amount);reason=str(reason).strip()
    if amount<0 or len(reason)<2 or len(reason)>200: raise ValueError('金額或原因不合法')
    with transaction() as db:
        _init(db);p=_parties(db,gid,eid)
        parties={str(p['brand_owner']),str(p['artist_owner'])}
        if len(parties)!=2: raise ValueError('同一持有人不可建立雙方和解協議')
        if actor not in parties or payer not in parties: raise PermissionError('僅允許代言雙方協議')
        term=db.execute('SELECT breach_amount,artist_approved,status FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        if not term or not term['artist_approved'] or int(term['breach_amount'])<amount: raise ValueError('金額不得超過雙方確認的合約違約金')
        payee=next(iter(parties-{payer}))
        old=db.execute('SELECT status FROM rp_endorsement_breach_settlements WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        if old: raise ValueError('此代言已有和解協議，不能覆蓋歷史')
        db.execute('INSERT INTO rp_endorsement_breach_settlements(guild_id,endorsement_id,payer_id,payee_id,amount,reason,proposed_by) VALUES(?,?,?,?,?,?,?)',(gid,eid,payer,payee,amount,reason,actor))
        _audit(db,gid,eid,'breach_proposed',actor,f'proposed amount={amount}; no transfer')
        return '✅ 和解提案已登記，待另一方確認；尚未扣款'

def accept(guild_id,endorsement_id,actor_id):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id)
    with transaction() as db:
        _init(db);p=_parties(db,gid,eid)
        parties={str(p['brand_owner']),str(p['artist_owner'])}
        row=db.execute('SELECT * FROM rp_endorsement_breach_settlements WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        if not row or row['status']!='proposed': raise ValueError('沒有待確認協議')
        if actor not in parties or actor==str(row['proposed_by']): raise PermissionError('須由另一方確認')
        db.execute("UPDATE rp_endorsement_breach_settlements SET status='accepted',accepted_by=? WHERE guild_id=? AND endorsement_id=?",(actor,gid,eid))
        _audit(db,gid,eid,'breach_accepted',actor,'雙方同意記錄，未執行付款')
        return '✅ 雙方協議已確認；付款須使用另行核准的安全金流，不會自動扣款'


def pay_settlement(guild_id, endorsement_id, actor_id, payer_character_id, payee_character_id):
    """雙方確認後，付款方親自授權從指定角色錢包轉帳；不可重複支付。"""
    from core.database import account_balance, get_or_create_account
    from core.economy import _payment
    gid, eid, actor = str(guild_id), int(endorsement_id), str(actor_id)
    with transaction() as db:
        _init(db)
        row = db.execute("SELECT * FROM rp_endorsement_breach_settlements WHERE guild_id=? AND endorsement_id=?", (gid,eid)).fetchone()
        if not row or row['status']!='accepted':
            raise ValueError('此和解尚未取得雙方確認')
        if str(row['payer_id']) != actor:
            raise PermissionError('只能由和解協議指定的付款人主動付款')
        payer=db.execute('SELECT id,owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,int(payer_character_id))).fetchone()
        payee=db.execute('SELECT id,owner_id,status FROM characters WHERE guild_id=? AND id=?',(gid,int(payee_character_id))).fetchone()
        if not payer or not payee or str(payer['owner_id'])!=actor or str(payee['owner_id'])!=str(row['payee_id']):
            raise PermissionError('角色持有人與和解雙方不符')
        if payer['status']!='active' or payee['status']!='active':
            raise ValueError('付款與收款角色必須啟用')
        if row['paid_at'] is not None:
            return 'ℹ️ 此和解已支付，不會重複扣款'
        amount=int(row['amount'])
        if amount<=0:
            db.execute("UPDATE rp_endorsement_breach_settlements SET paid_at=CURRENT_TIMESTAMP,payer_character_id=?,payee_character_id=? WHERE guild_id=? AND endorsement_id=?",(int(payer_character_id),int(payee_character_id),gid,eid))
            return '✅ 零金額協議已結清'
        source=get_or_create_account(db,gid,'character',str(payer_character_id),'wallet')
        dest=get_or_create_account(db,gid,'character',str(payee_character_id),'wallet')
        if account_balance(db,gid,source)<amount: raise ValueError('付款角色錢包餘額不足')
        ref=f'endorsement:settlement:{gid}:{eid}'
        result=_payment(db,gid,ref,source,dest,amount,f'代言和解 #{eid}')
        db.execute("UPDATE rp_endorsement_breach_settlements SET paid_at=CURRENT_TIMESTAMP,payer_character_id=?,payee_character_id=? WHERE guild_id=? AND endorsement_id=? AND status='accepted'",(int(payer_character_id),int(payee_character_id),gid,eid))
        _audit(db,gid,eid,'breach_paid',actor,f'amount={amount}; ref={ref}')
        return 'ℹ️ 已存在此筆付款識別碼，狀態已核對' if result.duplicated else f'✅ 已支付 {amount:,} 金幣'


def settlement_details(guild_id, endorsement_id, actor_id):
    gid,eid,actor=str(guild_id),int(endorsement_id),str(actor_id)
    with get_connection() as db:
        _init(db); p=_parties(db,gid,eid)
        if actor not in (str(p['brand_owner']),str(p['artist_owner'])): raise PermissionError('僅合約雙方可查看')
        row=db.execute('SELECT * FROM rp_endorsement_breach_settlements WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        return dict(row) if row else None
