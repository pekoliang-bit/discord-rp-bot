"""Financial notifications are queued after successful ledger commits; never disclose private amounts publicly."""
import logging
LOG=logging.getLogger(__name__)
def notify_payment(guild_id,business_id,character_id,amount,event,reference):
    try:
        from modules.notifications import queue_notification
        return queue_notification(int(guild_id),'private','bank',
            f'🏦 公司 #{business_id}｜{event}：{int(amount):,} 金幣。交易識別：{reference}',
            character_id=int(character_id),priority='high',dedupe_key=f'company-finance:{reference}')
    except Exception:
        LOG.exception('公司財務通知入列失敗（款項已以正式帳本為準）：%s', reference)
        return False
