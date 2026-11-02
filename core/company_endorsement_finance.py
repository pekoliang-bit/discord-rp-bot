"""代言合約期款：提案、藝人同意、品牌付款、解除與歷史。"""
from __future__ import annotations
import uuid
from datetime import date
from core.company_endorsement_term_guard import check_payment_term
from core.database import transaction, get_connection, account_balance, get_or_create_account
from core.economy import _payment
from modules.company_business import initialize as initialize_business
from modules.company_treasury import initialize_treasury
from core.company_endorsement_store import initialize_endorsements


def _init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_contract_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
        endorsement_id INTEGER NOT NULL, payer_business_id TEXT NOT NULL,
        character_id INTEGER NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0),
        installment_label TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'offered'
        CHECK(status IN ('offered','accepted','paid','cancelled')),
        created_by TEXT NOT NULL, accepted_by TEXT, paid_by TEXT,
        reference_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        accepted_at TEXT, paid_at TEXT, cancelled_at TEXT,
        UNIQUE(guild_id,endorsement_id,installment_label)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_endorsement_contract_payments ON rp_endorsement_contract_payments(guild_id,endorsement_id,status)")


def _endorsement(db, gid, eid):
    initialize_endorsements(db)
    row=db.execute("SELECT * FROM rp_company_endorsements WHERE guild_id=? AND id=? AND status='active'",(gid,int(eid))).fetchone()
    if row is None: raise ValueError('找不到有效代言；請先確認代言任命')
    return row


def _brand_and_payer(db,gid,endorsement,payer,actor):
    brand=db.execute("SELECT owner_id,parent_id FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='brand' AND status='active'",(gid,endorsement['brand_id'])).fetchone()
    entity=db.execute("SELECT owner_id,kind,status FROM rp_business_entities WHERE guild_id=? AND business_id=?",(gid,payer)).fetchone()
    if not brand or not entity or entity['status']!='active' or entity['kind'] not in ('brand','company'):
        raise ValueError('付款品牌或公司不存在')
    if str(brand['owner_id'])!=actor or str(entity['owner_id'])!=actor:
        raise PermissionError('必須由品牌及付款主體的持有人本人操作')
    if payer!=endorsement['brand_id'] and str(brand['parent_id'] or '')!=payer:
        raise PermissionError('付款公司必須是品牌的直接上層公司')
    return entity


def offer(guild_id,endorsement_id,payer_business_id,amount,label,actor_id,due_date=None):
    initialize_business();gid=str(guild_id);payer=str(payer_business_id);actor=str(actor_id)
    amount=int(amount);label=str(label).strip()
    if amount<=0 or not label or len(label)>80: raise ValueError('期款金額或名稱不合法')
    if due_date:
        try: due = date.fromisoformat(str(due_date)).isoformat()
        except (TypeError,ValueError): raise ValueError('期款到期日必須為 YYYY-MM-DD')
    else: due=None
    with transaction() as db:
        _init(db);_ensure_due_date(db);e=_endorsement(db,gid,endorsement_id)
        _brand_and_payer(db,gid,e,payer,actor)
        check_payment_term(db,gid,endorsement_id,due)
        ch=db.execute("SELECT owner_id FROM characters WHERE guild_id=? AND id=? AND status='active'",(gid,e['character_id'])).fetchone()
        if not ch: raise ValueError('代言角色未啟用')
        ref='endorsement:'+uuid.uuid4().hex
        cur=db.execute("INSERT INTO rp_endorsement_contract_payments(guild_id,endorsement_id,payer_business_id,character_id,amount,installment_label,created_by,reference_key) VALUES (?,?,?,?,?,?,?,?)",(gid,int(endorsement_id),payer,int(e['character_id']),amount,label,actor,ref))
        if due: db.execute('UPDATE rp_endorsement_contract_payments SET due_date=? WHERE id=?',(due,int(cur.lastrowid)))
        return int(cur.lastrowid)


def accept(guild_id,payment_id,actor_id):
    gid=str(guild_id);actor=str(actor_id)
    with transaction() as db:
        _init(db);_ensure_due_date(db);p=db.execute('SELECT * FROM rp_endorsement_contract_payments WHERE guild_id=? AND id=?',(gid,int(payment_id))).fetchone()
        if not p: raise ValueError('期款不存在')
        ch=db.execute("SELECT owner_id,status FROM characters WHERE guild_id=? AND id=?",(gid,p['character_id'])).fetchone()
        if not ch or ch['status']!='active' or str(ch['owner_id'])!=actor: raise PermissionError('只有代言角色持有人可確認期款')
        if p['status']=='accepted' or p['status']=='paid': return 'ℹ️ 此期款已確認'
        if p['status']!='offered': raise ValueError('此期款已取消')
        _endorsement(db,gid,p['endorsement_id'])
        db.execute("UPDATE rp_endorsement_contract_payments SET status='accepted',accepted_by=?,accepted_at=CURRENT_TIMESTAMP WHERE id=? AND status='offered'",(actor,int(payment_id)))
        return '✅ 期款已獲藝人確認，待品牌付款'


def pay(guild_id,payment_id,actor_id):
    initialize_treasury();gid=str(guild_id);actor=str(actor_id)
    with transaction() as db:
        _init(db);_ensure_due_date(db);p=db.execute('SELECT * FROM rp_endorsement_contract_payments WHERE guild_id=? AND id=?',(gid,int(payment_id))).fetchone()
        if not p: raise ValueError('期款不存在')
        if p['status']=='paid': return 'ℹ️ 此期款已付款，沒有重複扣款'
        if p['status']!='accepted': raise ValueError('需藝人確認期款後才能付款')
        e=_endorsement(db,gid,p['endorsement_id']);_brand_and_payer(db,gid,e,p['payer_business_id'],actor)
        check_payment_term(db,gid,p['endorsement_id'],p['due_date'])
        bank=db.execute("SELECT account_id,status FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?",(gid,p['payer_business_id'])).fetchone()
        if not bank or bank['status']!='active': raise ValueError('付款主體的銀行帳戶尚未啟用')
        source=int(bank['account_id']);dest=get_or_create_account(db,gid,'character',str(p['character_id']),'wallet')
        if account_balance(db,gid,source)<p['amount']: raise ValueError('公司銀行帳戶餘額不足')
        result=_payment(db,gid,p['reference_key'],source,dest,int(p['amount']),f"代言期款 #{p['id']} {p['installment_label']}")
        db.execute("UPDATE rp_endorsement_contract_payments SET status='paid',paid_by=?,paid_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=? AND status='accepted'",(actor,gid,int(payment_id)))
        return ('ℹ️ 付款識別碼已存在，已核對狀態' if result.duplicated else f"✅ 已付款 {p['amount']:,} 金幣至角色 #{p['character_id']}")


def cancel(guild_id,payment_id,actor_id):
    gid=str(guild_id);actor=str(actor_id)
    with transaction() as db:
        _init(db);_ensure_due_date(db);p=db.execute('SELECT * FROM rp_endorsement_contract_payments WHERE guild_id=? AND id=?',(gid,int(payment_id))).fetchone()
        if not p: raise ValueError('期款不存在')
        e=_endorsement(db,gid,p['endorsement_id']);_brand_and_payer(db,gid,e,p['payer_business_id'],actor)
        if p['status']=='paid': raise ValueError('已支付款項不能直接取消，需另行處理退款或違約')
        db.execute("UPDATE rp_endorsement_contract_payments SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('offered','accepted')",(int(payment_id),))
        return '✅ 未付款期款已取消，保留歷史紀錄'


def history(guild_id,endorsement_id,actor_id):
    gid=str(guild_id);actor=str(actor_id)
    with get_connection() as db:
        _init(db)
        e=_endorsement(db,gid,endorsement_id)
        brand=db.execute('SELECT owner_id FROM rp_business_entities WHERE guild_id=? AND business_id=?',(gid,e['brand_id'])).fetchone()
        ch=db.execute('SELECT owner_id FROM characters WHERE guild_id=? AND id=?',(gid,e['character_id'])).fetchone()
        if actor not in (str(brand['owner_id']),str(ch['owner_id'])): raise PermissionError('只有品牌及藝人本人可查看期款')
        return [dict(r) for r in db.execute('SELECT id,amount,installment_label,status,created_at,paid_at,due_date FROM rp_endorsement_contract_payments WHERE guild_id=? AND endorsement_id=? ORDER BY id DESC LIMIT 20',(gid,int(endorsement_id))).fetchall()]


def _ensure_due_date(db):
    fields={r['name'] for r in db.execute('PRAGMA table_info(rp_endorsement_contract_payments)').fetchall()}
    if 'due_date' not in fields:
        db.execute('ALTER TABLE rp_endorsement_contract_payments ADD COLUMN due_date TEXT')


def due_installments(guild_id,endorsement_id,actor_id):
    """依期款日期顯示逾期/即將到期，不會自行扣款。"""
    gid=str(guild_id)
    with get_connection() as db:
        _init(db);_ensure_due_date(db)
        e=_endorsement(db,gid,endorsement_id)
        brand=db.execute('SELECT owner_id FROM rp_business_entities WHERE guild_id=? AND business_id=?',(gid,e['brand_id'])).fetchone()
        ch=db.execute('SELECT owner_id FROM characters WHERE guild_id=? AND id=?',(gid,e['character_id'])).fetchone()
        if str(actor_id) not in (str(brand['owner_id']),str(ch['owner_id'])): raise PermissionError('只有合約雙方可查閱')
        return [dict(x) for x in db.execute("SELECT id,amount,installment_label,status,due_date FROM rp_endorsement_contract_payments WHERE guild_id=? AND endorsement_id=? AND status IN ('offered','accepted') ORDER BY COALESCE(due_date,'9999-12-31'), id LIMIT 30",(gid,int(endorsement_id))).fetchall()]
