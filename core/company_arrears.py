"""未付薪資查詢：依在職員工到期日及已發薪紀錄推導，無虛構債務。"""
from core.database import get_connection
from core.company_payroll_service import initialize_payroll
from core.company_payroll_period import payroll_period
from modules.company_treasury import _finance_permission

def arrears_summary(gid,bid,uid):
    from modules.company_workforce import initialize as initialize_workforce
    initialize_workforce();initialize_payroll();gid,bid,uid=map(str,(gid,bid,uid))
    with get_connection() as db:
        if not _finance_permission(db,gid,bid,uid): raise PermissionError('沒有財務查看權限')
        rule=db.execute('SELECT cycle_days FROM rp_company_payroll_settings WHERE guild_id=? AND business_id=?',(gid,bid)).fetchone()
        if not rule: return '尚未設定薪資週期，無法計算待發薪資。'
        rows=db.execute("SELECT e.character_id,e.hired_at,p.salary FROM company_employees e JOIN company_job_positions p ON p.guild_id=e.guild_id AND p.company_id=e.company_id AND p.rank=e.rank WHERE e.guild_id=? AND e.company_id=? AND e.status='active' ORDER BY e.character_id",(gid,bid)).fetchall()
        pending=[]
        for r in rows:
            period=payroll_period(r['hired_at'],int(rule['cycle_days']))
            if period is None: continue
            paid=db.execute('SELECT 1 FROM rp_company_payroll WHERE guild_id=? AND business_id=? AND character_id=? AND pay_period=?',(gid,bid,r['character_id'],period.key)).fetchone()
            if not paid: pending.append(f"角色 #{r['character_id']}：本期尚未發薪（基礎薪資 {int(r['salary']):,}）")
        lines=['📅 到期未發薪資（根據目前薪資期推導）',f'待處理人數：{len(pending)}',*pending[:25]]
        if len(pending)>25: lines.append(f'另有 {len(pending)-25} 人未列出')
        lines.append('註：此為最新到期薪資期清單，不是完整歷史欠薪債權帳。')
        return '\n'.join(lines)
