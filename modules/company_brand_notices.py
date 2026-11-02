"""品牌更新通知待發佇列，供未來 /通知 系統安全消費。"""
from core.database import get_connection
from modules.company_brand_progress import initialize

def pending_notices(guild_id=None, limit=50):
    initialize()
    limit=max(1,min(int(limit),200))
    query="SELECT id,guild_id,brand_id,event_type,content,created_at FROM rp_brand_notices WHERE delivered=0"
    params=[]
    if guild_id is not None:
        query += " AND guild_id=?"
        params.append(str(guild_id))
    query += " ORDER BY id LIMIT ?"
    params.append(limit)
    with get_connection() as db:
        return [dict(r) for r in db.execute(query,params).fetchall()]

def mark_delivered(notice_id):
    # 只能在通知系統已實際成功送達後呼叫。
    initialize()
    with get_connection() as db:
        cur=db.execute("UPDATE rp_brand_notices SET delivered=1 WHERE id=? AND delivered=0",(int(notice_id),))
        return cur.rowcount == 1
