"""期款到期提醒傳送至現有通知更新佇列。"""
from core.company_endorsement_reminders import pending,mark_forwarded

def forward_due_notices(limit=100):
    from modules.notification_event_bridge import publish_content_change
    count=0
    for r in pending(limit):
        publish_content_change(int(r['guild_id']), 'company', '代言合約期款提醒已更新（詳情請於公司商務管理查閱）',stable_id=r['event_key'],action='更新',source_url=None)
        mark_forwarded(r['id']);count+=1
    return count
