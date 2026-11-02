"""公司分潤：從真實準備金發放到同伺服器的角色錢包。"""
import time
from core.database import transaction, account_balance, get_or_create_account
from core.economy import _payment
from modules.company_treasury import initialize_treasury, _is_owner
from core.company_profit_reserve import reserve_account

def distribute(gid,bid,uid,character_id,amount,reference):
    initialize_treasury();gid,bid,uid=map(str,(gid,bid,uid));cid=int(character_id);amount=int(amount)
    if cid<=0 or amount<=0: raise ValueError('角色編號和金額必須大於零')
    with transaction() as db:
        if not _is_owner(db,gid,bid,uid): raise PermissionError('只有主要持有人可以發起分潤')
        recipient=db.execute("SELECT id FROM characters WHERE guild_id=? AND id=? AND status NOT IN ('deleted','retired')",(gid,cid)).fetchone()
        if not recipient: raise ValueError('找不到此伺服器的有效角色')
        source=reserve_account(db,gid,bid)
        if account_balance(db,gid,source)<amount: raise ValueError('分潤準備金不足')
        target=get_or_create_account(db,gid,'character',str(cid),'wallet')
        key='company-dividend:'+str(reference)
        result=_payment(db,gid,key,source,target,amount,f'公司 {bid} 分潤｜角色 #{cid}')
        if result.duplicated: return 'ℹ️ 此筆分潤已發放，不重複入帳。'
        db.execute('INSERT INTO rp_company_finance_audit (guild_id,business_id,actor_id,action,reference_key,amount,target_type,target_id,note,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(gid,bid,uid,'dividend',key,amount,'character',str(cid),'準備金分潤入帳',int(time.time())))
    from core.company_finance_events import notify_payment
    notify_payment(gid,bid,cid,amount,'分潤入帳',key)
    return f'✅ 已發放分潤 {amount:,} 金幣至角色 #{cid} 錢包。'
