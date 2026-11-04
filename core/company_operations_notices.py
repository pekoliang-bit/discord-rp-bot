
"""Publish non-sensitive company operation changes to the notification digest.

Only non-financial metadata is announced; exact salaries, amounts and delegates
never enter the public digest. Failed publication remains retryable.
"""
from __future__ import annotations
import logging
from core.database import get_connection, transaction
LOG=logging.getLogger(__name__)

ALLOWED_ACTIONS = {'manager_permission','group_attach_company','employee_hired',
                   'employee_exit','contract_signed','contract_ended','contract_renewed'}


def _setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rp_company_notice_mirror (
        guild_id TEXT NOT NULL, source_key TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(guild_id,source_key))''')


def sync(limit=100):
    from modules.notifications import record_update
    from modules.company_business import initialize as initialize_business
    initialize_business()
    limit=max(1,min(300,int(limit)))
    with get_connection() as db:
        _setup(db)
        exists=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rp_business_audit'").fetchone())
        if not exists:return 0
        rows=[dict(r) for r in db.execute('''SELECT a.id,a.guild_id,a.action FROM rp_business_audit a
          WHERE a.action IN (?,?,?,?,?,?,?) AND NOT EXISTS
          (SELECT 1 FROM rp_company_notice_mirror m
           WHERE m.guild_id=a.guild_id AND m.source_key='audit:'||a.id)
          ORDER BY a.id LIMIT ?''',(*sorted(ALLOWED_ACTIONS),limit))]
    processed=0
    for r in rows:
        gid=str(r['guild_id']); key='audit:'+str(r['id'])
        try:
            record_update(int(gid),'company','公司經營資料異動','更新',update_key=f'company-event:{gid}:{key}')
        except Exception:
            LOG.exception('公司異動通知排入失敗：%s',key)
            continue
        with transaction() as db:
            _setup(db)
            db.execute('INSERT OR IGNORE INTO rp_company_notice_mirror(guild_id,source_key) VALUES (?,?)',(gid,key))
        processed+=1
    return processed
