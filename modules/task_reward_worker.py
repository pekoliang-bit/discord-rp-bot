"""任務獎勵補發工作入口。重用唯一的結算實作，不再複製結算程式。"""
from __future__ import annotations
import logging
from modules.task_rewards import initialize_rewards, pending_reward_rows, settle_reward

LOG=logging.getLogger(__name__)


def retry_pending(guild_id, *, limit=100):
    """Run from a trusted maintenance job, never expose to regular users."""
    initialize_rewards()
    result={'attempted':0,'claimed':0,'pending':0,'errors':[]}
    for row in pending_reward_rows(guild_id,limit):
        result['attempted']+=1
        try:
            outcome=settle_reward(row['guild_id'],row['character_id'],row['task_id'],row['cycle_key'])
            result['claimed' if outcome.get('claimed') else 'pending']+=1
        except Exception as exc:
            LOG.exception('任務獎勵補發失敗：%s',dict(row))
            result['pending']+=1
            result['errors'].append({'task_id':row['task_id'],'character_id':row['character_id'],'error':str(exc)[:180]})
    return result
