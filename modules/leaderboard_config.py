"""排行榜正式啟用錨點：不得在重啟時自動重設。"""
from core.database import transaction,get_connection
from modules.leaderboard_storage import initialize,audit
from modules.leaderboard_cycle import parse,iso

def configure(guild_id,anchor_utc,enabled=False):
    initialize();anchor=iso(parse(anchor_utc))
    with transaction() as db:
        existing=db.execute('SELECT anchor_utc FROM lb_v11_config WHERE guild_id=?',(str(guild_id),)).fetchone()
        if existing and existing['anchor_utc']!=anchor:raise ValueError('錨點已存在；不可無聲變更')
        db.execute('INSERT INTO lb_v11_config(guild_id,anchor_utc,enabled) VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled',(str(guild_id),anchor,int(bool(enabled))))
    audit(guild_id,'configure',{'anchor':anchor,'enabled':enabled})
def get_config(guild_id):
    initialize()
    with get_connection() as db:
        row=db.execute('SELECT anchor_utc,enabled FROM lb_v11_config WHERE guild_id=?',(str(guild_id),)).fetchone()
    return dict(row) if row else None
