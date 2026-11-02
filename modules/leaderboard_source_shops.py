"""個人商鋪榜：過去 30 天銷售扣退款，唯讀。"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from core.database import get_connection
from modules.leaderboard_source_common import table_columns, ranked

SHOPS={'id','guild_id','name','status'}
SALES={'guild_id','shop_id','amount','kind','occurred_at'}

def query(guild_id,limit=100,*,as_of=None):
    end=as_of or datetime.now(timezone.utc)
    if end.tzinfo is None: raise ValueError('as_of 必須含時區')
    start=end-timedelta(days=30)
    with get_connection() as db:
        if not SHOPS <= table_columns(db,'personal_shops') or not SALES <= table_columns(db,'personal_shop_sales'):
            return [],'unavailable'
        rows=db.execute("""SELECT sh.id,sh.name,
            COALESCE(SUM(CASE WHEN sa.kind='sale' THEN sa.amount WHEN sa.kind='refund' THEN -sa.amount ELSE 0 END),0) AS score
            FROM personal_shops sh LEFT JOIN personal_shop_sales sa
              ON sa.guild_id=sh.guild_id AND sa.shop_id=sh.id
              AND datetime(sa.occurred_at)>=datetime(?) AND datetime(sa.occurred_at)<datetime(?)
            WHERE sh.guild_id=? AND sh.status='open'
            GROUP BY sh.id,sh.name""",
            (start.isoformat(sep=' '),end.isoformat(sep=' '),str(guild_id))).fetchall()
    data=[{'id':r['id'],'name':r['name'],'score':int(r['score'])} for r in rows]
    return (ranked(data,limit),'ready') if data else ([], 'empty')
