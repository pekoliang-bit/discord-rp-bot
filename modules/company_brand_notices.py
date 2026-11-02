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


def sync_to_notification_updates(limit=100):
    """把正式品牌異動轉入既有通知彙總紀錄（非 Discord 送達確認）。

    使用 stable key 避免重複；若通知系統無法寫入則保留原始待發紀錄。
    """
    from modules.notification_event_bridge import publish_content_change
    inserted = 0
    for notice in pending_notices(limit=limit):
        event_key = f"brand-notice:{notice['id']}"
        action = '更新'
        try:
            publish_content_change(
                int(notice['guild_id']), 'company', str(notice['content']),
                source_url=None, stable_id=event_key, action=action,
            )
        except Exception:
            # 中斷保留原始佇列，下次再試；不要吞掉故障。
            raise
        # 已安全寫進通知更新佇列即可標記已轉交；不代表已送達玩家。
        mark_delivered(notice['id'])
        inserted += 1
    return inserted
