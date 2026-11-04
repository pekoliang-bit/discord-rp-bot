
"""Company-financed rumor PR: permission check and funding provenance.
Used inside the same SQLite transaction as the official rumor charge/effect.
"""
from __future__ import annotations


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_pr_funding (
        guild_id TEXT NOT NULL, request_key TEXT NOT NULL, business_id TEXT NOT NULL,
        actor_id TEXT NOT NULL, charge_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        PRIMARY KEY(guild_id,request_key), UNIQUE(guild_id,charge_id))''')


def verify_company_payer(db,gid,bid,actor_id,rumor_id,cost):
    from modules.company_business import has_permission
    gid,bid,uid=map(str,(gid,bid,actor_id))
    case=db.execute('SELECT business_id FROM rp_company_rumors WHERE guild_id=? AND id=?',(gid,int(rumor_id))).fetchone()
    if not case or str(case['business_id'])!=bid:
        raise PermissionError('此傳聞不屬於指定公司')
    entity=db.execute('SELECT owner_id,kind,status FROM rp_business_entities WHERE guild_id=? AND business_id=?',(gid,bid)).fetchone()
    if not entity or entity['status']!='active' or entity['kind'] not in ('company','brand'):
        raise ValueError('公司或品牌不存在或已停用')
    if str(entity['owner_id'])!=uid and not has_permission(db,gid,bid,uid,'public_relations'):
        raise PermissionError('缺少這間公司的公關處理權限')
    if str(entity['owner_id'])!=uid:
        # PR delegation alone is not a blanket finance grant. Finance permission
        # and a configured per-payment limit are required to spend company funds.
        from modules.company_treasury import _manager_limit
        if not has_permission(db,gid,bid,uid,'finance') or _manager_limit(db,gid,bid,uid)<cost:
            raise PermissionError('管理層須同時持有財務權限及足夠單筆支出額度')
    bank=db.execute('SELECT account_id,status FROM rp_company_bank_accounts WHERE guild_id=? AND company_id=?',(gid,bid)).fetchone()
    if not bank or bank['status']!='active':
        raise ValueError('公司銀行帳戶尚未開通或已凍結')
    return int(bank['account_id'])
