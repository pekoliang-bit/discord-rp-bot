"""派獎前只讀資格檢查，保留正式派獎開關且不能直接開帳戶。"""
from __future__ import annotations
from modules.leaderboard_payout_policy import PAYOUT_ENABLED,inspect_reward
from modules.leaderboard_recipient_resolution import resolve


def inspect(board,entity_key,reward,*,db=None,guild_id=None):
    basic=inspect_reward(board,entity_key,reward)
    issues=list(basic['issues']);recipient=None
    try:
        recipient=resolve(board,entity_key,db=db,guild_id=guild_id)
        if recipient['requires_verified_adapter']:
            issues.append('非角色正式派獎程式尚未授權啟用')
        if recipient['entity_type']=='character' and not recipient['entity_id'].isdecimal():
            issues.append('角色 ID 必須為正式數字識別碼')
        if db is not None:
            account=recipient['account']
            issues.extend(account['issues'])
            if not account['can_credit']:
                issues.append('尚無已驗證的正式收款帳戶')
    except (ValueError,TypeError,KeyError) as exc:issues.append(str(exc))
    return {'allowed':not issues,'issues':list(dict.fromkeys(issues)),
            'recipient':recipient,'payout_enabled':bool(PAYOUT_ENABLED)}
