"""正式派發前只讀資格報告。任何未知規則都會封鎖。"""
from __future__ import annotations
from modules.leaderboard_payout_policy import PAYOUT_ENABLED,inspect_reward
from modules.leaderboard_recipient_resolution import resolve
def inspect(board,entity_key,reward):
    basic=inspect_reward(board,entity_key,reward)
    issues=list(basic['issues']);recipient=None
    try:recipient=resolve(board,entity_key)
    except (ValueError,TypeError,KeyError) as exc:issues.append(str(exc))
    if recipient and recipient['requires_verified_adapter']:
        issues.append('非角色獲獎單位尚未完成正式帳戶映射與交易適配')
    if recipient and recipient['entity_type']=='character' and not recipient['entity_id'].isdecimal():
        issues.append('角色 ID 應為正式數字識別碼')
    return {'allowed':not issues,'issues':list(dict.fromkeys(issues)),'recipient':recipient,'payout_enabled':bool(PAYOUT_ENABLED)}
