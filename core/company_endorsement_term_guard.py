"""期款與已確認合約的期限狀態一致性檢查。"""
from datetime import date

def check_payment_term(db,guild_id,endorsement_id,due_date=None):
    row=db.execute('SELECT start_date,end_date,status,artist_approved FROM rp_endorsement_terms WHERE guild_id=? AND endorsement_id=?',(str(guild_id),int(endorsement_id))).fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_endorsement_terms'").fetchone() else None
    if row is None:
        return  # 舊代言尚未建立條款，保留向後相容
    if row['status']!='active' or not row['artist_approved']:
        raise ValueError('代言條款尚未經雙方確認，暫不能新增或支付期款')
    if date.fromisoformat(row['end_date'])<date.today():
        raise ValueError('代言條款已到期，請先完成續約')
    if due_date and (date.fromisoformat(due_date)<date.fromisoformat(row['start_date']) or date.fromisoformat(due_date)>date.fromisoformat(row['end_date'])):
        raise ValueError('期款到期日必須在已確認的合約期間內')
