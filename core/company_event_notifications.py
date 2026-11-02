"""Company event adapters; notifications are best-effort, never part of ledger commits."""
from __future__ import annotations
import logging
LOG = logging.getLogger(__name__)


def private_wage_event(guild_id, character_id, debt_id, event, amount):
    """Queue to the character's private forum notification post, not Discord DM."""
    if event not in ("registered", "paid"):
        raise ValueError("Unsupported wage event")
    action = "已核定歷史欠薪" if event == "registered" else "已補發歷史欠薪"
    try:
        from modules.notifications import queue_notification
        return queue_notification(
            int(guild_id), "private", "bank",
            f"🏦 {action}，紀錄 #{int(debt_id)}，金額 {int(amount):,} 金幣。",
            character_id=int(character_id), priority="high",
            dedupe_key=f"company-wage:{guild_id}:{debt_id}:{event}",
        )
    except Exception:
        LOG.exception("Unable to queue wage notification: %s", debt_id)
        return False


def group_delegation_update(guild_id, group_id, action):
    """Only non-sensitive group changes go to the aggregate update log."""
    if action not in ("granted", "revoked"):
        raise ValueError("Unsupported delegation action")
    try:
        from modules.notifications import record_update
        # The existing digest should not expose delegate IDs or capabilities.
        return record_update(
            int(guild_id), "company", "集團管理權限調整", "更新",
            update_key=f"group-delegation:{guild_id}:{group_id}:{action}:{__import__('time').time_ns()}",
        )
    except Exception:
        LOG.exception("Unable to record group delegation update: %s", group_id)
        return False
