"""代言期款到期提醒：寫入持久佇列，不自動扣款。"""
from datetime import date, timedelta
from core.database import transaction, get_connection
from core.company_endorsement_finance import _init, _ensure_due_date


def initialize(db):
    _init(db); _ensure_due_date(db)
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_due_alerts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
      payment_id INTEGER NOT NULL, alert_kind TEXT NOT NULL,
      event_key TEXT NOT NULL UNIQUE, message TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, forwarded_at TEXT,
      UNIQUE(guild_id,payment_id,alert_kind))""")


def scan_due(today=None, window_days=3):
    today = date.fromisoformat(str(today)) if today is not None else date.today()
    end = (today + timedelta(days=window_days)).isoformat()
    count=0
    with transaction() as db:
        initialize(db)
        records=db.execute("""SELECT p.id,p.guild_id,p.endorsement_id,p.installment_label,p.amount,p.due_date
            FROM rp_endorsement_contract_payments p
            WHERE p.status='accepted' AND p.due_date IS NOT NULL AND p.due_date<=?
            ORDER BY p.due_date,p.id""",(end,)).fetchall()
        for r in records:
            due=date.fromisoformat(r['due_date']); kind='overdue' if due<today else 'due'
            # 每一期同種提醒最多一次，避免五分鐘排程洗版。
            key=f"endorsement-payment:{r['guild_id']}:{r['id']}:{kind}"
            message=f"代言 #{r['endorsement_id']} 期款 #{r['id']}（{r['installment_label']}）{r['amount']:,} 金幣，應付日 {r['due_date']}" + ('，已逾期' if kind=='overdue' else '，即將到期')
            result=db.execute("INSERT OR IGNORE INTO rp_endorsement_due_alerts(guild_id,payment_id,alert_kind,event_key,message) VALUES(?,?,?,?,?)",(r['guild_id'],r['id'],kind,key,message))
            count+=result.rowcount
    return count


def pending(limit=100):
    with get_connection() as db:
        initialize(db)
        return [dict(r) for r in db.execute('SELECT * FROM rp_endorsement_due_alerts WHERE forwarded_at IS NULL ORDER BY id LIMIT ?', (max(1,min(int(limit),200)),)).fetchall()]


def mark_forwarded(alert_id):
    with transaction() as db:
        initialize(db)
        db.execute('UPDATE rp_endorsement_due_alerts SET forwarded_at=CURRENT_TIMESTAMP WHERE id=? AND forwarded_at IS NULL',(int(alert_id),))
