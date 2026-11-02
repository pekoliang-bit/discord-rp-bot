"""Historical unpaid completed payroll periods (derived, not fictional ledger debt)."""
from datetime import timedelta
from core.database import get_connection
from core.company_payroll_service import initialize_payroll
from core.company_payroll_period import payroll_period,parse_time
from modules.company_treasury import _finance_permission

def arrears_summary(gid,bid,uid):
    from modules.company_workforce import initialize as initialize_workforce
    initialize_workforce();initialize_payroll();gid,bid,uid=map(str,(gid,bid,uid))
    with get_connection() as db:
        if not _finance_permission(db,gid,bid,uid): raise PermissionError('沒有財務查看權限')
        rule=db.execute('SELECT cycle_days FROM rp_company_payroll_settings WHERE guild_id=? AND business_id=?',(gid,bid)).fetchone()
        if not rule: return '尚未設定薪資週期，無法計算歷史欠薪。'
        days=int(rule['cycle_days']);step=timedelta(days=days)
        rows=db.execute("SELECT e.character_id,e.hired_at,p.salary FROM company_employees e JOIN company_job_positions p ON p.guild_id=e.guild_id AND p.company_id=e.company_id AND p.rank=e.rank WHERE e.guild_id=? AND e.company_id=? AND e.status='active' ORDER BY e.character_id",(gid,bid)).fetchall()
        lines=[f'📋 公司 #{bid}｜歷史到期未發薪資（每{days}天）'];periods=0;people=0
        for r in rows:
            latest=payroll_period(r['hired_at'],days)
            if latest is None: continue
            hired=parse_time(r['hired_at']); count=int((latest.end-hired)//step)
            if count>520:
                lines.append(f"角色 #{r['character_id']}：超過520期，需要人工核對");continue
            anchor=hired.strftime('%Y%m%dT%H%M%S%fZ')
            paid={x['pay_period'] for x in db.execute('SELECT pay_period FROM rp_company_payroll WHERE guild_id=? AND business_id=? AND character_id=?',(gid,bid,r['character_id'])).fetchall()}
            if any(len(k)==7 and k[4]=='-' for k in paid):
                lines.append(f"角色 #{r['character_id']}：有舊制月薪，需先遷移");continue
            missing=[i for i in range(1,count+1) if f'{days}d:{anchor}:{i}' not in paid]
            if missing:
                people+=1;periods+=len(missing)
                lines.append(f"角色 #{r['character_id']}：未付 {len(missing)} 期，最早第 {missing[0]} 期（當前基礎薪資 {int(r['salary']):,}）")
        lines.insert(1,f'待處理：{people} 名員工，共 {periods} 期')
        lines.append('使用「發放薪資」將優先補發最早未付款期。')
        lines.append('注意：舊薪資／離職員工／歷史調薪尚無完整快照，數字只供核對，不能視作已核定債權。')
        return '\n'.join(lines)[:1900]
