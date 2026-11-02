"""品牌升級狀態及最近事件查詢；不修改資產。"""
from modules.company_brand_progress import initialize
from core.database import get_connection

def recent_upgrade_checks(guild_id, brand_id, limit=10):
    initialize()
    limit=max(1,min(int(limit),50))
    with get_connection() as db:
        return [dict(r) for r in db.execute(
            "SELECT check_date,result,details FROM rp_brand_upgrade_runs WHERE guild_id=? AND brand_id=? ORDER BY check_date DESC LIMIT ?",
            (str(guild_id),str(brand_id),limit)).fetchall()]

def recent_brand_sales(guild_id, brand_id, limit=10):
    initialize()
    limit=max(1,min(int(limit),50))
    with get_connection() as db:
        return [dict(r) for r in db.execute(
            "SELECT event_key,kind,amount,reference_key,created_at FROM rp_brand_sales WHERE guild_id=? AND brand_id=? ORDER BY created_at DESC LIMIT ?",
            (str(guild_id),str(brand_id),limit)).fetchall()]
