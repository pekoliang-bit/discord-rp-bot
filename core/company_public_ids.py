
"""跨模組公開公司與合約編號，不更改既有資料庫主鍵。"""
from __future__ import annotations

from core.database import transaction


def prefix(guild_id):
    raw = str(guild_id)
    if not raw.isdigit() or len(raw) < 5:
        raise ValueError("伺服器 ID 格式無效")
    return raw[:2] + raw[-3:]


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_company_public_codes (
        guild_id TEXT NOT NULL, business_id TEXT NOT NULL,
        public_code TEXT NOT NULL, ordinal INTEGER NOT NULL,
        PRIMARY KEY(guild_id,business_id), UNIQUE(guild_id,public_code),
        UNIQUE(guild_id,ordinal))""")
    db.execute("""CREATE TABLE IF NOT EXISTS rp_company_contract_codes (
        guild_id TEXT NOT NULL, contract_id INTEGER NOT NULL,
        company_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
        PRIMARY KEY(guild_id,contract_id),
        UNIQUE(guild_id,company_id,ordinal))""")


def _next(db, gid):
    return int(db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM rp_company_public_codes WHERE guild_id=?", (gid,)).fetchone()[0])


def ensure_companies(db, guild_id):
    """先登記舊公司及集團，依建立時間固定編號；品牌沿用原編號。"""
    setup(db)
    gid = str(guild_id)
    existing = {row[0] for row in db.execute(
        "SELECT business_id FROM rp_company_public_codes WHERE guild_id=?", (gid,))}
    legacy = db.execute("""SELECT company_id FROM rp_approved_company_authorizations
        WHERE guild_id=? AND status='approved' ORDER BY rowid""", (gid,)).fetchall()
    entities = db.execute("""SELECT business_id FROM rp_business_entities
        WHERE guild_id=? AND kind IN ('company','group')
        ORDER BY founded_at, rowid""", (gid,)).fetchall()
    for row in [*legacy, *entities]:
        bid = str(row[0])
        if bid in existing:
            continue
        n = _next(db, gid)
        db.execute("INSERT INTO rp_company_public_codes VALUES (?,?,?,?)",
                   (gid, bid, f'{prefix(gid)}-{n}', n))
        existing.add(bid)


def public_company(db, guild_id, business_id):
    gid, bid = str(guild_id), str(business_id)
    ensure_companies(db, gid)
    row = db.execute("SELECT public_code FROM rp_company_public_codes WHERE guild_id=? AND business_id=?",
                     (gid, bid)).fetchone()
    return row[0] if row else bid


def resolve_company(db, guild_id, supplied):
    gid, supplied = str(guild_id), str(supplied).strip()
    ensure_companies(db, gid)
    row = db.execute("SELECT business_id FROM rp_company_public_codes WHERE guild_id=? AND public_code=?",
                     (gid, supplied)).fetchone()
    return str(row[0]) if row else supplied


def ensure_contracts(db, guild_id, company_id):
    setup(db)
    gid, bid = str(guild_id), str(company_id)
    contracts = db.execute("""SELECT id FROM rp_company_employee_contracts
        WHERE guild_id=? AND company_id=? ORDER BY id""", (gid, bid)).fetchall()
    current = {int(row[0]) for row in db.execute(
        "SELECT contract_id FROM rp_company_contract_codes WHERE guild_id=? AND company_id=?", (gid, bid))}
    n = int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM rp_company_contract_codes WHERE guild_id=? AND company_id=?",
                       (gid, bid)).fetchone()[0])
    for contract in contracts:
        cid = int(contract[0])
        if cid in current:
            continue
        n += 1
        db.execute("INSERT INTO rp_company_contract_codes VALUES(?,?,?,?)", (gid, cid, bid, n))


def contract_code(db, guild_id, contract_id):
    from core.company_contract_store import initialize_contracts
    initialize_contracts(db)
    gid, cid = str(guild_id), int(contract_id)
    row = db.execute("SELECT company_id FROM rp_company_employee_contracts WHERE guild_id=? AND id=?",
                     (gid, cid)).fetchone()
    if row is None:
        raise LookupError("找不到合約")
    bid = str(row[0])
    ensure_contracts(db, gid, bid)
    ordinal = db.execute("SELECT ordinal FROM rp_company_contract_codes WHERE guild_id=? AND contract_id=?",
                         (gid, cid)).fetchone()[0]
    return f'{public_company(db, gid, bid)}-{ordinal}'


def resolve_contract(db, guild_id, supplied):
    """接受新式顯示編號及舊整數編號，內部交易仍使用原始 id。"""
    from core.company_contract_store import initialize_contracts
    initialize_contracts(db)
    gid = str(guild_id)
    raw = str(supplied).strip()
    if raw.isdigit():
        return int(raw)
    company_code, sep, ordinal_text = raw.rpartition('-')
    if not sep or not ordinal_text.isdigit() or int(ordinal_text) < 1:
        raise ValueError("合約編號格式錯誤")
    bid = resolve_company(db, gid, company_code)
    ensure_contracts(db, gid, bid)
    row = db.execute("""SELECT contract_id FROM rp_company_contract_codes
        WHERE guild_id=? AND company_id=? AND ordinal=?""", (gid, bid, int(ordinal_text))).fetchone()
    if row is None:
        raise LookupError("找不到合約")
    return int(row[0])


def list_companies(guild_id, kinds=('company','group')):
    """僅回傳此服有效主體，供下拉選單使用。"""
    from core.database import transaction
    gid = str(guild_id)
    with transaction() as db:
        ensure_companies(db, gid)
        placeholders = ','.join('?' for _ in kinds)
        rows = db.execute(f"""SELECT business_id,name,kind FROM rp_business_entities
            WHERE guild_id=? AND status='active' AND kind IN ({placeholders})
            ORDER BY founded_at, rowid""", (gid, *kinds)).fetchall()
        result = [(str(row['business_id']), str(row['name']), str(row['kind']),
                   public_company(db, gid, row['business_id'])) for row in rows]
        if 'company' in kinds:
            existing = {item[0] for item in result}
            for old in db.execute("""SELECT a.company_id
                FROM rp_approved_company_authorizations a
                WHERE a.guild_id=? AND a.status='approved'
                  AND NOT EXISTS (
                    SELECT 1 FROM rp_business_entities b
                    WHERE b.guild_id=a.guild_id AND b.business_id=a.company_id
                  )
                ORDER BY a.rowid""", (gid,)):
                bid = str(old[0])
                if bid not in existing:
                    result.append((bid, bid, 'company', public_company(db, gid, bid)))
        return result
