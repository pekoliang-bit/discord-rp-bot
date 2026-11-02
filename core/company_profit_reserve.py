"""公司分潤準備金：只能由銀行實際餘額撥入，不使用銷售估計創造資金。"""
import time
from core.database import transaction, get_connection, account_balance, get_or_create_account
from core.economy import _payment
from modules.company_treasury import initialize_treasury, _is_owner, _finance_permission

def reserve_account(db,gid,bid):
    return get_or_create_account(db,str(gid),'organization',str(bid),'dividend_reserve')

def reserve_balance(gid,bid,uid):
    initialize_treasury()
    with get_connection() as db:
        if not _finance_permission(db,str(gid),str(bid),str(uid)): raise PermissionError('沒有財務查閱權限')
        return int(account_balance(db,str(gid),reserve_account(db,gid,bid)))

def allocate_profit(gid,bid,uid,amount,reference):
    initialize_treasury(); amount=int(amount); gid,bid,uid=map(str,(gid,bid,uid))
    if amount<=0: raise ValueError('金額必須大於零')
    with transaction() as db:
        if not _is_owner(db,gid,bid,uid): raise PermissionError('只有公司持有人可以核定分潤準備金')
        bank=db.execute('SELECT account_id,status FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?',(gid,bid)).fetchone()
        if not bank or bank['status']!='active': raise ValueError('公司銀行帳戶未開通或已凍結')
        source=int(bank['account_id']); dest=reserve_account(db,gid,bid)
        if account_balance(db,gid,source)<amount: raise ValueError('銀行餘額不足')
        key='profit-reserve:'+str(reference)
        result=_payment(db,gid,key,source,dest,amount,'公司核定盈餘撥入分潤準備金')
        if result.duplicated: return 'ℹ️ 本次撥款已處理，不重複扣款。'
        db.execute('INSERT INTO rp_company_finance_audit (guild_id,business_id,actor_id,action,reference_key,amount,target_type,target_id,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(gid,bid,uid,'profit_reserve',key,amount,'organization',bid,'持有人核定撥入；非自動判定會計淨利',int(time.time())))
    return f'✅ 已從公司銀行撥入分潤準備金 {amount:,} 金幣。'
