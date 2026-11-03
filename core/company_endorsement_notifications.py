"""代言事件→角色專屬論壇『通知』帖子。
本模組只將事件加入既有 notification_outbox；實際 Discord 發送由 /通知 dispatcher 完成。
沒有目標通知帖時保留 pending，不改用 Discord 私訊。
"""
from __future__ import annotations
import logging
from core.database import get_connection, transaction
LOG=logging.getLogger(__name__)


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rp_endorsement_notice_mirror (
      guild_id TEXT NOT NULL,event_key TEXT NOT NULL,fingerprint TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(guild_id,event_key))""")


def _read_events(db, limit):
    events=[]
    def has(name):
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
    if not has('rp_company_endorsements') or not has('characters'):
        return events
    # 頭銜狀態：避免 UI 與狀態不同步；角色僅收到自己的事件。
    for r in db.execute("""SELECT e.guild_id,e.id,e.character_id,e.tier,e.scope,e.status,e.ended_at
        FROM rp_company_endorsements e ORDER BY e.id DESC LIMIT ?""",(limit,)):
        key=f"appointment:{r['id']}"
        fp=f"{r['status']}:{r['ended_at'] or ''}"
        msg=(f"🎤 代言 #{r['id']} T{r['tier']}（{r['scope']}）已生效；正式條款請至 /公司 確認。"
             if r['status']=='active' else f"📜 代言 #{r['id']} 已結束，歷史合約與待付期款仍可查詢。")
        events.append((r['guild_id'],r['character_id'],key,fp,msg))
    if has('rp_endorsement_appointment_invites'):
        for r in db.execute("""SELECT guild_id,id,character_id,brand_id,tier,status,decided_at
            FROM rp_endorsement_appointment_invites ORDER BY id DESC LIMIT ?""",(limit,)):
            key=f"invite:{r['id']}";fp=f"{r['status']}:{r['decided_at'] or ''}"
            events.append((r['guild_id'],r['character_id'],key,fp,
                f"📨 品牌 {r['brand_id']} 的 T{r['tier']} 代言邀約 #{r['id']}：{r['status']}。請到 /公司 → 商務管理查看。"))
    if has('rp_endorsement_term_history'):
        for r in db.execute("""SELECT h.id,h.guild_id,h.endorsement_id,h.event,e.character_id
            FROM rp_endorsement_term_history h JOIN rp_company_endorsements e
            ON e.guild_id=h.guild_id AND e.id=h.endorsement_id
            ORDER BY h.id DESC LIMIT ?""",(limit,)):
            key=f"term-history:{r['id']}"
            events.append((r['guild_id'],r['character_id'],key,'new',
                f"📅 代言 #{r['endorsement_id']} 合約狀態更新：{r['event']}。請至 /公司 → 代言合約查閱。"))
    if has('rp_endorsement_contract_payments'):
        for r in db.execute("""SELECT id,guild_id,character_id,endorsement_id,status,amount,
            accepted_at,paid_at,cancelled_at FROM rp_endorsement_contract_payments ORDER BY id DESC LIMIT ?""",(limit,)):
            key=f"payment:{r['id']}"
            fp=f"{r['status']}:{r['accepted_at'] or ''}:{r['paid_at'] or ''}:{r['cancelled_at'] or ''}"
            events.append((r['guild_id'],r['character_id'],key,fp,
                f"💰 代言 #{r['endorsement_id']} 期款 #{r['id']}：{r['status']}，金額 {int(r['amount']):,} 金幣。"))
    if has('rp_endorsement_due_alerts'):
        for r in db.execute("""SELECT a.guild_id,a.id,a.event_key,a.alert_kind,a.payment_id,p.character_id
            FROM rp_endorsement_due_alerts a JOIN rp_endorsement_contract_payments p
            ON p.guild_id=a.guild_id AND p.id=a.payment_id ORDER BY a.id DESC LIMIT ?""",(limit,)):
            events.append((r['guild_id'],r['character_id'],f"due:{r['event_key']}",'new',
                f"⏰ 代言期款 #{r['payment_id']}：{'已逾期' if r['alert_kind']=='overdue' else '即將到期'}，請至 /公司 查看。"))
    if has('rp_endorsement_breach_settlements'):
        for r in db.execute("""SELECT b.guild_id,b.endorsement_id,b.status,b.accepted_by,b.paid_at,e.character_id
            FROM rp_endorsement_breach_settlements b JOIN rp_company_endorsements e
            ON e.guild_id=b.guild_id AND e.id=b.endorsement_id ORDER BY b.endorsement_id DESC LIMIT ?""",(limit,)):
            status='paid' if r['paid_at'] else r['status']
            events.append((r['guild_id'],r['character_id'],f"settlement:{r['endorsement_id']}",
                f"{status}:{r['accepted_by'] or ''}:{r['paid_at'] or ''}",
                f"⚖️ 代言 #{r['endorsement_id']} 違約和解狀態：{status}。請至 /公司 查詢。"))
    return events


def sync_private_notices(limit=200):
    from modules.notifications import initialize as initialize_notifications, queue_notification
    initialize_notifications()
    with get_connection() as db:
        _setup(db)
        events=_read_events(db,max(1,min(int(limit),500)))
        seen={ (str(r['guild_id']),r['event_key']):r['fingerprint'] for r in
            db.execute("SELECT guild_id,event_key,fingerprint FROM rp_endorsement_notice_mirror") }
    total=0
    for gid,cid,key,fp,message in events:
        gid=str(gid); cid=int(cid)
        if seen.get((gid,key))==fp: continue
        # 版本作為 dedupe_key；同一事件更新狀態才重新通知。
        unique=f"endorsement:{gid}:{key}:{fp}"
        try:
            queue_notification(int(gid),'private','company',message,
                character_id=cid,priority='high',dedupe_key=unique)
            # 隊列寫入成功或使用者明確關閉此類通知時，才視為已處理。
            with transaction() as db:
                _setup(db)
                db.execute("""INSERT INTO rp_endorsement_notice_mirror(guild_id,event_key,fingerprint)
                    VALUES(?,?,?) ON CONFLICT(guild_id,event_key)
                    DO UPDATE SET fingerprint=excluded.fingerprint,updated_at=CURRENT_TIMESTAMP""",(gid,key,fp))
            total+=1
        except Exception:
            LOG.exception('代言私人通知入列失敗：%s',key)
    return total
