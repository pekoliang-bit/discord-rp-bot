"""代言合約期限、續約、違約協議：不自動扣款，需雙方核可。"""
from __future__ import annotations
from datetime import date
from core.database import transaction, get_connection
from core.company_endorsement_store import initialize_endorsements


def _setup(db):
    initialize_endorsements(db)
    db.execute('''CREATE TABLE IF NOT EXISTS rp_endorsement_terms (
      guild_id TEXT NOT NULL, endorsement_id INTEGER NOT NULL, start_date TEXT NOT NULL,
      end_date TEXT NOT NULL, breach_amount INTEGER NOT NULL DEFAULT 0 CHECK(breach_amount>=0),
      version INTEGER NOT NULL DEFAULT 1, proposer_id TEXT NOT NULL,
      artist_approved INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'proposed'
      CHECK(status IN ('proposed','active','expired','terminated')),
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,endorsement_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_endorsement_term_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, endorsement_id INTEGER NOT NULL,
      event TEXT NOT NULL, actor_id TEXT NOT NULL, details TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def _parties(db,gid,eid):
    row=db.execute('''SELECT e.*,b.owner_id AS brand_owner,c.owner_id AS artist_owner
      FROM rp_company_endorsements e
      JOIN rp_business_entities b ON b.guild_id=e.guild_id AND b.business_id=e.brand_id
      JOIN characters c ON c.guild_id=e.guild_id AND c.id=e.character_id
      WHERE e.guild_id=? AND e.id=?''',(gid,eid)).fetchone()
    if not row: raise ValueError('找不到代言紀錄')
    return row


def _audit(db,gid,eid,event,actor,detail):
    db.execute('INSERT INTO rp_endorsement_term_history(guild_id,endorsement_id,event,actor_id,details) VALUES(?,?,?,?,?)',(gid,eid,event,actor,detail))


def propose(guild_id,endorsement_id,actor_id,end_date,breach_amount=0):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id)
    try: date.fromisoformat(end_date)
    except (ValueError,TypeError): raise ValueError('日期格式必須為 YYYY-MM-DD')
    if date.fromisoformat(end_date)<date.today(): raise ValueError('截止日期不能早於今天')
    amount=int(breach_amount)
    if amount<0: raise ValueError('違約金不可為負數')
    with transaction() as db:
        _setup(db);p=_parties(db,gid,eid)
        if str(p['brand_owner'])!=actor: raise PermissionError('只有品牌持有人可提出期限及條款')
        if p['status']!='active': raise ValueError('此代言已結束，不能續約')
        old=db.execute('SELECT version FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        version=int(old['version'])+1 if old else 1
        db.execute('''INSERT INTO rp_endorsement_terms(guild_id,endorsement_id,start_date,end_date,breach_amount,version,proposer_id,artist_approved,status)
          VALUES(?,?,?,?,?,?,?,0,'proposed') ON CONFLICT(guild_id,endorsement_id)
          DO UPDATE SET end_date=excluded.end_date,breach_amount=excluded.breach_amount,
          version=excluded.version,proposer_id=excluded.proposer_id,artist_approved=0,status='proposed',updated_at=CURRENT_TIMESTAMP''',
          (gid,eid,date.today().isoformat(),end_date,amount,version,actor))
        _audit(db,gid,eid,'propose',actor,f'v{version} expiry={end_date} agreed_penalty={amount}')
        return version


def approve(guild_id,endorsement_id,actor_id):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id)
    with transaction() as db:
        _setup(db);p=_parties(db,gid,eid)
        if str(p['artist_owner'])!=actor: raise PermissionError('只有代言角色持有人可同意合約條款')
        term=db.execute('SELECT * FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        if not term or term['status']!='proposed': raise ValueError('目前沒有待確認的合約條款')
        if p['status']!='active': raise ValueError('代言已結束')
        db.execute("UPDATE rp_endorsement_terms SET artist_approved=1,status='active',updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND endorsement_id=?",(gid,eid))
        _audit(db,gid,eid,'approve',actor,f"v{term['version']}")
        return '✅ 合約期限及違約金已經藝人確認'


def terminate(guild_id,endorsement_id,actor_id,reason):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id);reason=str(reason).strip()
    if not reason or len(reason)>200: raise ValueError('請填寫 1–200 字的結束理由')
    with transaction() as db:
        _setup(db);p=_parties(db,gid,eid)
        if actor not in (str(p['brand_owner']),str(p['artist_owner'])): raise PermissionError('只有品牌或藝人本人可提交終止紀錄')
        term=db.execute('SELECT * FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        if p['status']!='active': raise ValueError('代言已結束')
        db.execute("UPDATE rp_company_endorsements SET status='ended',ended_at=CURRENT_TIMESTAMP,end_reason=? WHERE guild_id=? AND id=?",(reason,gid,eid))
        db.execute("UPDATE rp_endorsement_terms SET status='terminated',updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND endorsement_id=?",(gid,eid))
        agreed=int(term['breach_amount']) if term and term['status']=='active' and term['artist_approved'] else 0
        _audit(db,gid,eid,'terminate',actor,f'{reason}; agreed_penalty={agreed}; no automatic payment')
        return {'agreed_breach_amount':agreed,'notice':'僅記錄雙方已同意的違約金，未自動扣款或判定責任'}


def details(guild_id,endorsement_id,actor_id):
    gid=str(guild_id);eid=int(endorsement_id);actor=str(actor_id)
    with get_connection() as db:
        _setup(db);p=_parties(db,gid,eid)
        if actor not in (str(p['brand_owner']),str(p['artist_owner'])): raise PermissionError('無權查看此合約')
        term=db.execute('SELECT * FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(gid,eid)).fetchone()
        return dict(term) if term else None
