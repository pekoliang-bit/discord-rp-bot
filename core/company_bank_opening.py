"""公司銀行帳戶正式開戶（在申請審核交易內執行）。"""
from core.database import get_or_create_account


def initialize_company_bank(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS rp_company_bank_accounts (
            guild_id TEXT NOT NULL,
            company_id TEXT NOT NULL,
            account_id INTEGER NOT NULL,
            opened_by TEXT NOT NULL,
            application_id INTEGER NOT NULL,
            initial_deposit INTEGER NOT NULL DEFAULT 0 CHECK(initial_deposit >= 0),
            opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','frozen')),
            PRIMARY KEY(guild_id,company_id),
            UNIQUE(guild_id,account_id),
            UNIQUE(guild_id,application_id)
        )
    """)
    columns = {row['name'] for row in db.execute('PRAGMA table_info(rp_company_bank_accounts)')}
    required = {'guild_id','company_id','account_id','opened_by','application_id','opened_at','status'}
    if not required <= columns:
        raise RuntimeError('既有公司銀行帳戶資料表結構不同，請先檢查遷移，禁止直接開戶')
    if 'initial_deposit' not in columns:
        db.execute('ALTER TABLE rp_company_bank_accounts ADD COLUMN initial_deposit INTEGER NOT NULL DEFAULT 0')


def approve_company_bank(db, guild_id, company_id, applicant_id, application_id):
    gid, bid, uid = str(guild_id).strip(), str(company_id).strip(), str(applicant_id).strip()
    if not (gid and bid and uid) or isinstance(application_id, bool):
        raise ValueError('公司開戶資料不完整')
    app_id = int(application_id)
    if app_id <= 0:
        raise ValueError('申請編號必須是正整數')
    initialize_company_bank(db)
    from modules.application_extras import validate_company_bank_applicant
    validate_company_bank_applicant(db, gid, bid, uid)
    company = db.execute("""
        SELECT business_id,kind FROM rp_business_entities
        WHERE guild_id=? AND business_id=? AND kind IN ('company','group') AND status='active'
    """, (gid, bid)).fetchone()
    if company is None:
        raise ValueError('公司／集團不存在或尚未啟用')
    existing = db.execute("""
        SELECT account_id,application_id FROM rp_company_bank_accounts
        WHERE guild_id=? AND company_id=?
    """, (gid, bid)).fetchone()
    if existing is not None:
        if int(existing['application_id']) == app_id:
            return int(existing['account_id'])
        raise ValueError('此公司／集團已開立銀行帳戶')
    account_id = get_or_create_account(db,gid,'organization',bid,'bank')
    db.execute("""
        INSERT INTO rp_company_bank_accounts
            (guild_id,company_id,account_id,opened_by,application_id,initial_deposit)
        VALUES (?,?,?,?,?,0)
    """, (gid,bid,account_id,uid,app_id))
    return int(account_id)
