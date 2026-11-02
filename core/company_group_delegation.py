"""集團有限授權：董事長指派；預設只開放查閱，金庫仍遵循原權限。"""
from core.database import transaction, get_connection
from core.company_group_overview import group_overview


def _setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_group_delegations (
      guild_id TEXT NOT NULL, group_id TEXT NOT NULL, delegate_id TEXT NOT NULL,
      capability TEXT NOT NULL CHECK(capability IN ('overview','member_report')),
      granted_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,group_id,delegate_id,capability))''')


def grant(guild_id,group_id,actor_id,delegate_id,capability='overview'):
    gid=str(guild_id);group_id=str(group_id);actor=str(actor_id);delegate=str(delegate_id)
    if capability not in ('overview','member_report'): raise ValueError('不支援此權限；財務管理不能經此授權')
    if actor==delegate: raise ValueError('無須授權自己')
    owned=group_overview(gid,actor)
    if str(owned.get('group_id'))!=group_id: raise PermissionError('只有目前集團董事長可授權')
    with transaction() as db:
        _setup(db)
        db.execute('INSERT OR IGNORE INTO rp_group_delegations(guild_id,group_id,delegate_id,capability,granted_by) VALUES(?,?,?,?,?)',(gid,group_id,delegate,capability,actor))
    return '✅ 已授權集團資訊查閱（不含金庫或人事修改）'


def revoke(guild_id,group_id,actor_id,delegate_id,capability='overview'):
    gid=str(guild_id);group_id=str(group_id);actor=str(actor_id)
    if str(group_overview(gid,actor).get('group_id'))!=group_id: raise PermissionError('只有集團董事長可取消授權')
    with transaction() as db:
        _setup(db)
        cur=db.execute('DELETE FROM rp_group_delegations WHERE guild_id=? AND group_id=? AND delegate_id=? AND capability=?',(gid,group_id,str(delegate_id),capability))
        return bool(cur.rowcount)


def list_grants(guild_id,group_id,actor_id):
    gid=str(guild_id);group_id=str(group_id);actor=str(actor_id)
    if str(group_overview(gid,actor).get('group_id'))!=group_id: raise PermissionError('只有集團董事長可查看授權名單')
    with get_connection() as db:
        _setup(db)
        return [dict(r) for r in db.execute('SELECT delegate_id,capability,created_at FROM rp_group_delegations WHERE guild_id=? AND group_id=? ORDER BY created_at DESC',(gid,group_id))]


def has_capability(guild_id,group_id,actor_id,capability='overview'):
    if capability not in ('overview','member_report'): return False
    with get_connection() as db:
        _setup(db)
        group=db.execute("SELECT owner_id,status FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='group'",(str(guild_id),str(group_id))).fetchone()
        if not group or group['status']!='active': return False
        if str(group['owner_id'])==str(actor_id): return True
        return db.execute('SELECT 1 FROM rp_group_delegations WHERE guild_id=? AND group_id=? AND delegate_id=? AND capability=?',(str(guild_id),str(group_id),str(actor_id),capability)).fetchone() is not None
