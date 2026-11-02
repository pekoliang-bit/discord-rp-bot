
"""Audited, manually confirmed historical wages; never infer past salaries from current rank."""
from __future__ import annotations
import time
from core.database import transaction, get_connection, get_or_create_account
from core.economy import _payment


def _setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_wage_debt (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, business_id TEXT NOT NULL,
      character_id INTEGER NOT NULL, period_label TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0),
      salary_snapshot INTEGER NOT NULL CHECK(salary_snapshot>=0), reason TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid')),
      approved_by TEXT NOT NULL, approved_at INTEGER NOT NULL, paid_by TEXT,
      paid_at INTEGER, reference_key TEXT, UNIQUE(guild_id,business_id,character_id,period_label))''')


def record_debt(guild_id,business_id,actor_id,character_id,period_label,amount,reason):
    from modules.company_treasury import _is_owner, initialize_treasury
    initialize_treasury()
    gid,bid,uid=str(guild_id),str(business_id),str(actor_id)
    cid=int(character_id); amount=int(amount); label=period_label.strip(); reason=reason.strip()
    if cid<=0 or amount<=0 or amount>10**12 or not 1<=len(label)<=80 or not 5<=len(reason)<=300:
        raise ValueError('角色、金額、薪資期或核定依據不正確')
    with transaction() as db:
        _setup(db)
        if not _is_owner(db,gid,bid,uid): raise PermissionError('僅公司持有人可以核定歷史欠薪')
        if not db.execute('SELECT 1 FROM characters WHERE guild_id=? AND id=?',(gid,cid)).fetchone():
            raise ValueError('角色不存在')
        prior=db.execute('SELECT id FROM rp_company_wage_debt WHERE guild_id=? AND business_id=? AND character_id=? AND period_label=?',(gid,bid,cid,label)).fetchone()
        if prior: raise ValueError('這個角色的該薪資期已核定，禁止重複建立')
        db.execute('''INSERT INTO rp_company_wage_debt(guild_id,business_id,character_id,period_label,amount,salary_snapshot,reason,approved_by,approved_at)
          VALUES(?,?,?,?,?,?,?,?,?)''',(gid,bid,cid,label,amount,amount,reason,uid,int(time.time())))
    return '✅ 已登記歷史欠薪（尚未付款）。離職角色亦保留此紀錄。'


def list_debts(guild_id,business_id,actor_id):
    from modules.company_treasury import _finance_permission,initialize_treasury
    initialize_treasury();gid,bid,uid=str(guild_id),str(business_id),str(actor_id)
    with get_connection() as db:
        _setup(db)
        if not _finance_permission(db,gid,bid,uid):raise PermissionError('沒有查看公司財務權限')
        return [dict(r) for r in db.execute('''SELECT id,character_id,period_label,amount,status,reason,paid_at
          FROM rp_company_wage_debt WHERE guild_id=? AND business_id=? ORDER BY id DESC LIMIT 30''',(gid,bid))]


def settle_debt(guild_id,business_id,actor_id,debt_id):
    from modules.company_treasury import _is_owner,initialize_treasury
    initialize_treasury();gid,bid,uid=str(guild_id),str(business_id),str(actor_id)
    with transaction() as db:
        _setup(db)
        if not _is_owner(db,gid,bid,uid): raise PermissionError('只允許公司持有人清償歷史欠薪')
        row=db.execute('SELECT * FROM rp_company_wage_debt WHERE id=? AND guild_id=? AND business_id=?',(int(debt_id),gid,bid)).fetchone()
        if not row: raise ValueError('欠薪紀錄不存在')
        if row['status']=='paid': return 'ℹ️ 本筆欠薪已清償'
        bank=db.execute('SELECT account_id,status FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?',(gid,bid)).fetchone()
        if not bank or bank['status']!='active': raise ValueError('公司銀行帳戶未開通或已凍結')
        ref=f'company-wage-debt:{gid}:{bid}:{row["id"]}'
        wallet=get_or_create_account(db,gid,'character',str(row['character_id']),'wallet')
        result=_payment(db,gid,ref,int(bank['account_id']),wallet,int(row['amount']),f'公司歷史欠薪補發：{row["period_label"]}')
        if result.duplicated: raise RuntimeError('帳本存在付款，但欠薪仍未標記，請人工核對')
        db.execute('UPDATE rp_company_wage_debt SET status=?,paid_by=?,paid_at=?,reference_key=? WHERE id=?',('paid',uid,int(time.time()),ref,row['id']))
    return f'✅ 已補發 {int(row["amount"]):,} 金幣，角色 #{row["character_id"]}；交易已記帳。'
