
"""Conservative, manually evidenced cash-basis profit classification.

Only authenticated owners may classify company ledger movements. Classification
never alters balances, does not create receivables, and never authorizes dividends.
"""
from __future__ import annotations
import time
from core.database import get_connection, transaction
from core.company_cashflow_reconcile import ledger_movements

INCOME = {'operating_income', 'other_income'}
EXPENSE = {'refund', 'cost_of_sales', 'payroll', 'operating_expense', 'tax'}
NEUTRAL = {'loan', 'equity', 'loan_repayment', 'owner_withdrawal', 'other_nonoperating'}
CATEGORIES = INCOME | EXPENSE | NEUTRAL


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_accounting_categories (
        guild_id TEXT NOT NULL, business_id TEXT NOT NULL, transaction_id INTEGER NOT NULL,
        category TEXT NOT NULL, classified_by TEXT NOT NULL, classified_at INTEGER NOT NULL,
        reason TEXT NOT NULL, PRIMARY KEY(guild_id,business_id,transaction_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_accounting_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, business_id TEXT NOT NULL,
        transaction_id INTEGER NOT NULL, old_category TEXT, new_category TEXT NOT NULL,
        actor_id TEXT NOT NULL, reason TEXT NOT NULL, changed_at INTEGER NOT NULL)''')


def classify(guild_id, business_id, actor_id, transaction_id, category, reason):
    from modules.company_treasury import _is_owner, initialize_treasury
    initialize_treasury()
    gid,bid,uid = map(str,(guild_id,business_id,actor_id))
    tid = int(transaction_id)
    category = str(category).strip()
    reason = str(reason).strip()
    if category not in CATEGORIES or len(reason)<8 or len(reason)>500:
        raise ValueError('科目不正確或核定依據不足（8～500字）')
    with transaction() as db:
        initialize(db)
        if not _is_owner(db,gid,bid,uid):
            raise PermissionError('只有公司持有人可以分類交易')
        movement = next((m for m in ledger_movements(db,gid,bid) if int(m['transaction_id'])==tid),None)
        if not movement or int(movement['delta'])==0:
            raise ValueError('找不到這間公司實際對外的金流；內部轉帳不得分類')
        delta=int(movement['delta'])
        if category in INCOME|{'loan','equity'} and delta<=0:
            raise ValueError('入帳科目只能用於公司實際流入')
        if category in EXPENSE|{'loan_repayment','owner_withdrawal'} and delta>=0:
            raise ValueError('支出科目只能用於公司實際流出')
        old=db.execute('SELECT category FROM rp_company_accounting_categories WHERE guild_id=? AND business_id=? AND transaction_id=?',(gid,bid,tid)).fetchone()
        db.execute('''INSERT INTO rp_company_accounting_categories (guild_id,business_id,transaction_id,category,classified_by,classified_at,reason)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(guild_id,business_id,transaction_id)
            DO UPDATE SET category=excluded.category,classified_by=excluded.classified_by,classified_at=excluded.classified_at,reason=excluded.reason''',
            (gid,bid,tid,category,uid,int(time.time()),reason))
        db.execute('''INSERT INTO rp_company_accounting_audit
            (guild_id,business_id,transaction_id,old_category,new_category,actor_id,reason,changed_at)
            VALUES (?,?,?,?,?,?,?,?)''',(gid,bid,tid,old['category'] if old else None,category,uid,reason,int(time.time())))
    return f'✅ 已將交易 #{tid} 分類為 {category}（不影響現金餘額）'


def report(guild_id,business_id,actor_id):
    from modules.company_treasury import _finance_permission, initialize_treasury
    initialize_treasury()
    gid,bid,uid=map(str,(guild_id,business_id,actor_id))
    with get_connection() as db:
        initialize(db)
        if not _finance_permission(db,gid,bid,uid):
            raise PermissionError('沒有財務查閱權限')
        movements=ledger_movements(db,gid,bid)
        categories={int(r['transaction_id']):r['category'] for r in db.execute(
            'SELECT transaction_id,category FROM rp_company_accounting_categories WHERE guild_id=? AND business_id=?',(gid,bid))}
    totals={k:0 for k in CATEGORIES}
    pending=[]
    for m in movements:
        delta=int(m['delta']); tid=int(m['transaction_id'])
        if delta==0: continue
        kind=categories.get(tid)
        if kind in CATEGORIES: totals[kind]+=abs(delta)
        else: pending.append(m)
    profit=sum(totals[k] for k in INCOME)-sum(totals[k] for k in EXPENSE)
    return {'cash_basis_result':profit,'totals':totals,'pending':pending,'complete':not pending,
            'nonoperating':sum(totals[k] for k in NEUTRAL),'cash_movement_count':len(movements)}


def format_report(guild_id,business_id,actor_id):
    r=report(guild_id,business_id,actor_id)
    lines=[f'📚 公司 {business_id}｜已分類現金收支', f'已分類收支試算：{r["cash_basis_result"]:+,} 金幣',
           f'未分類對外交易：{len(r["pending"])} 筆',
           f'不計入收益／費用的金流：{r["nonoperating"]:,} 金幣']
    for k in sorted(CATEGORIES):
        if r['totals'][k]:lines.append(f'・{k}: {r["totals"][k]:,}')
    for m in r['pending'][:8]: lines.append(f'待分類 #{m["transaction_id"]} {m["delta"]:+,}｜{str(m["key"])[:44]}')
    lines.append('⚠️ 這是收付實現制分類試算，不含應收應付、存貨成本及折舊；不可直接當成正式會計淨利潤或分紅額度。')
    return '\n'.join(lines)
