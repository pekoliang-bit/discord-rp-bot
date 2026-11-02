"""雙方協議的代言違約和解紀錄；不自動認定違約方或扣款。"""
from core.database import transaction, get_connection
from core.company_endorsement_lifecycle import _setup, _parties, _audit

def _init(db):
    _setup(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_breach_settlements (
    guild_id TEXT NOT NULL, endorsement_id INTEGER NOT NULL, payer_id TEXT NOT NULL,
    payee_id TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0),
    reason TEXT NOT NULL, proposed_by TEXT NOT NULL, accepted_by TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','accepted','cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(guild_id,endorsement_id))""")

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
